"""Bounded tier-first claims, weighted node placement and fenced leases."""
from datetime import timedelta
from dataclasses import dataclass
import hmac
from sqlalchemy import and_, case, func, literal_column, or_, select, text, update
from .assets import available
from .config import settings
from .errors import ProcessingError
from .models import Asset, Attempt, ClassicState, Job, now, uid
from .queue_models import ComputeNode, ExecutionLease, JobStage, SchedulerMutex
from .translation_models import TranslationProvider
from .translation_provider_limits import request_limit, unavailable_providers
from .translation_fonts import DEFAULT_FONTS, supports_font

ACTIVE = {"awaiting_upload", "validating_upload", "queued", "running", "outcome_unknown"}
IMAGE_STAGES = {"page"}
CLAIM_BATCH_LIMIT = 4
CANDIDATE_LIMIT = 32


def lock_scheduler(db):
    # Cross-resource maintenance and bootstrap take this before any row locks.
    # A savepoint rollback can release a lock first acquired inside that savepoint.
    transaction = db.get_nested_transaction() or db.get_transaction() or db.begin()
    if db.info.get('scheduler_lock_transaction') is transaction:
        return
    if db.get_bind().dialect.name == "postgresql":
        db.execute(text("SELECT pg_advisory_xact_lock(761349211)"))
    else:
        db.execute(update(SchedulerMutex).where(SchedulerMutex.id == 1).values(revision=SchedulerMutex.revision))
    db.info['scheduler_lock_transaction'] = transaction


def write_rows(db, query, *, skip_locked=False):
    if db.get_bind().dialect.name != 'postgresql':
        lock_scheduler(db)  # SQLite ignores FOR UPDATE; acquire its writer first.
    return db.scalars(query.with_for_update(key_share=True, skip_locked=skip_locked).execution_options(populate_existing=True))


def locked_node(db, node_id):
    return write_rows(db, select(ComputeNode).where(ComputeNode.id == node_id)).one_or_none()


def lock_jobs(db, ids):
    # Node (if needed), then job IDs in order. Read stages/leases in a fresh statement after waiting.
    write_rows(db, select(Job.id).where(Job.id.in_(ids)).order_by(Job.id)).all()


def lock_lease_jobs(db, lease_ids, node_id=None):
    # A completed lease only replays its receipt; a retry gets a new lease ID.
    jobs = select(ExecutionLease.job_id).where(ExecutionLease.id.in_(lease_ids), ExecutionLease.completed_at.is_(None))
    lock_jobs(db, jobs.where(ExecutionLease.node_id == node_id) if node_id is not None else jobs)


def touch_job(db, job):
    job.changed_at = now()
    from .notifications import publish
    publish(db, 'user:' + job.owner_id)
    publish(db, 'compute')


def ensure_stages(db, job):
    if not job.input_asset_id or job.status not in {"queued", "running"}:
        return
    names = ["page", "text"]
    existing = {s.name for s in db.scalars(select(JobStage).where(JobStage.job_id == job.id))}
    for name in names:
        if name not in existing:
            db.add(JobStage.for_job(job, name=name, status="ready" if name == names[0] else "waiting"))
    if job.mode == "classic" and db.get(ClassicState, job.id) is None:
        db.add(ClassicState(job_id=job.id))
    touch_job(db, job)
    db.flush()


def current_lease(db, lease_id, lease_token=None):
    lease = db.get(ExecutionLease, lease_id)
    if not lease or (lease_token is not None and not hmac.compare_digest(lease.token, lease_token)):
        raise ProcessingError("LEASE_EXPIRED", "执行授权已失效")
    stage = db.get(JobStage, lease.stage_id)
    job = db.get(Job, lease.job_id)
    check_current_lease(lease, stage, job)
    return lease, stage, job


def check_current_lease(lease, stage, job):
    if lease.completed_at or lease.expires_at <= now():
        raise ProcessingError("LEASE_EXPIRED", "执行授权已失效")
    if (not stage or stage.generation != lease.generation or stage.status != "running" or not job
            or job.status != "running" or job.cancel_requested or job.discard_output):
        raise ProcessingError("LEASE_EXPIRED", "任务已停止或执行代次已更新")


def pool_for(stage):
    return "image" if stage.name in IMAGE_STAGES else "upload" if stage.name == "validate_upload" else stage.name


def _eligible_stages(node, stages, at, *, stage_ids=None, blocked_providers=()):
    """Shared admission predicates, without ordering."""
    image_stage = JobStage.name.in_(IMAGE_STAGES)
    jobs = (select(Job.id).select_from(Job)
        .outerjoin(Asset, Asset.id == Job.input_asset_id)
        .where(Job.id == JobStage.job_id, Job.workflow_kind == 'image', Job.status.in_(["queued", "running", "validating_upload"]),
            Job.cancel_requested.is_(False), Job.discard_output.is_(False),
            or_(JobStage.name == "validate_upload", and_(Asset.id.is_not(None), Asset.deleted_at.is_(None),
                Asset.purged_at.is_(None), or_(Asset.expires_at.is_(None), Asset.expires_at > at, Asset.active_references > 0))),
            or_(~image_stage, and_(Job.target_language.in_(node.supported_languages),
                Job.config["engine"]["protocol_version"].as_integer() == 3))))
    if not (node.runtime_report or {}).get('overlay_tiles'):
        result_format = Job.config['result_format'].as_string()
        jobs = jobs.where(or_(JobStage.name != 'page', result_format.is_(None), result_format != 'overlay-tiles-v1'))
    if 'page' in stages:
        font_id = Job.config['font_id'].as_string()
        jobs = jobs.where(or_(~image_stage, supports_font(font_id, Job.target_language,
            (node.runtime_report or {}).get('supported_font_ids', []))))
    if "text" in stages:
        full, paid_only = blocked_providers or ((), ())
        jobs = (jobs.outerjoin(TranslationProvider, TranslationProvider.id == Job.config['text']['provider_id'].as_string())
            .where(or_(JobStage.name != 'text', and_(TranslationProvider.enabled.is_(True),
                TranslationProvider.id.not_in(full),
                or_(JobStage.service_priority > 0, TranslationProvider.id.not_in(paid_only))))))
    # OFFSET 0 keeps PostgreSQL from flattening the per-stage predicate into a
    # jobs-first join that sorts the entire eligible backlog before LIMIT.
    eligible = select(JobStage.id).where(JobStage.status == literal_column("'ready'"), JobStage.name.in_(stages),
        JobStage.available_at <= at, jobs.correlate(JobStage).offset(0).exists())
    return eligible.where(JobStage.id.in_(stage_ids)) if stage_ids is not None else eligible


def _candidate_rows(db, node, stages, at, *, stage_ids=None, materialize=True, blocked_providers=(), min_priority=0):
    query = _eligible_stages(node, stages, at, stage_ids=stage_ids, blocked_providers=blocked_providers)
    if min_priority:
        query = query.where(JobStage.service_priority >= min_priority)
    elif not materialize:
        # Do not fill a short paid batch by searching through ineligible ordinary work.
        paid = query.where(JobStage.service_priority == 1).correlate(None).exists()
        query = query.where(JobStage.service_priority == case((paid, 1), else_=0))
    if materialize:
        query = query.join(Job, Job.id == JobStage.job_id)
    projection = (JobStage, Job) if materialize else (JobStage.id, JobStage.generation, JobStage.service_priority)
    query = query.with_only_columns(*projection).order_by(
        JobStage.service_priority.desc(), JobStage.available_at, JobStage.id).limit(CANDIDATE_LIMIT)
    return db.execute(query.execution_options(populate_existing=True)).all()


def paid_reserve(node):
    configured = (node.desired_config or {}).get('paid_reserved_slots')
    return int(node.capacity > 1) if configured is None else min(configured, node.capacity)


def _preselect(db, node, allowed_stages, blocked_providers=(), min_priority=0):
    # Reuse the caller's connection, including its uncommitted nodes/pages.
    # A second Session can deadlock a full pool while every claimant holds its
    # first connection. PostgreSQL READ COMMITTED rechecks after the row locks;
    # our SQLite driver does not BEGIN on SELECT, while pending DML already
    # owns its writer lock. Transaction ownership always stays with the caller.
    stages = set(node.capabilities) & set(allowed_stages or node.capabilities)
    if not node.enabled or not stages:
        return {}
    rows = _candidate_rows(db, node, stages, now(), materialize=False, blocked_providers=blocked_providers, min_priority=min_priority)
    return {row.id: (row.generation, row.service_priority) for row in rows}


def _blocked_providers(db, node, allowed_stages):
    if not node or 'text' not in (set(node.capabilities) & set(allowed_stages or node.capabilities)):
        return []
    return unavailable_providers(db.execute(select(TranslationProvider.id, TranslationProvider.requests_per_minute)
        .where(TranslationProvider.enabled.is_(True))).all())


def remaining_capacity(db, node):
    if not node or not node.enabled:
        return 0
    busy = db.scalar(select(func.count()).select_from(ExecutionLease).where(
        ExecutionLease.node_id == node.id, ExecutionLease.completed_at.is_(None)))
    return max(0, node.capacity - busy)


def slot_budget(db, node):
    """Free slots and the subset available to ordinary work, including unreleased leases."""
    if not node or not node.enabled:
        return 0, 0
    reserve = paid_reserve(node)
    if not reserve:
        free = remaining_capacity(db, node)
        return free, free
    busy, ordinary = db.execute(select(func.count(), func.count().filter(JobStage.service_priority == 0))
        .select_from(ExecutionLease).join(JobStage).where(ExecutionLease.node_id == node.id,
            ExecutionLease.completed_at.is_(None))).one()
    free = max(0, node.capacity - busy)
    return free, min(free, max(0, node.capacity - reserve - ordinary))


def compute_loads(db, node, at):
    """One live-node/load snapshot; unreconciled expired leases still occupy slots."""
    peers = db.execute(select(ComputeNode, func.count(ExecutionLease.id),
        func.count(ExecutionLease.id).filter(ExecutionLease.resource_pool.in_(
            ('doc_prepare', 'doc_ocr', 'doc_render', 'doc_zip'))),
        func.count(ExecutionLease.id).filter(JobStage.service_priority == 0))
        .outerjoin(ExecutionLease, and_(ExecutionLease.node_id == ComputeNode.id,
            ExecutionLease.completed_at.is_(None)))
        .outerjoin(JobStage, JobStage.id == ExecutionLease.stage_id)
        .where(ComputeNode.enabled.is_(True), ComputeNode.engine_version != 'control',
            ComputeNode.config_error.is_(None),
            or_(ComputeNode.id == node.id, and_(
                ComputeNode.heartbeat_at > at - timedelta(seconds=settings().cluster_node_timeout_seconds),
                ComputeNode.applied_config_version == ComputeNode.config_version,
                ComputeNode.runtime_report['protocol_version'].as_integer() == 3,
                ComputeNode.runtime_report['ready'].as_boolean().is_(True),
                 ComputeNode.runtime_report['channel_id'].as_string().is_not(None),
                 ComputeNode.runtime_report['dispatch_until'].as_string() > at.isoformat() + 'Z')))
        .group_by(ComputeNode.id).execution_options(populate_existing=True)).all()
    return {peer.id: (peer, busy, documents, ordinary) for peer, busy, documents, ordinary in peers if busy < peer.capacity}


def assigned_candidates(node, candidates, loads, *, document=False, steal_before=None):
    """Simulate bounded weighted least-load placement; only the claimant gets leases."""
    from .node_config import NodeConfig
    configs = {key: NodeConfig.model_validate(load[0].desired_config) for key, load in loads.items()}
    planned = {key: 0 for key in loads}
    assigned = set()
    fallback = None
    for stage, job in candidates:
        eligible = []
        font = job.config.get('font_id') or DEFAULT_FONTS.get(job.target_language)
        for peer_id, (peer, busy, documents, ordinary) in loads.items():
            config, extra = configs[peer_id], planned[peer_id]
            runtime = peer.runtime_report or {}
            if (busy + extra >= peer.capacity
                    or (not stage.service_priority and ordinary + extra >= peer.capacity - paid_reserve(peer))
                    or (peer_id != node.id and extra >= runtime.get(
                         'dispatch_documents' if document else 'dispatch_slots', 0))
                    or ('doc_prepare' if document else 'page') not in peer.capabilities
                    or job.target_language not in peer.supported_languages
                    or font not in runtime.get('supported_font_ids', [])
                    or (document and (runtime.get('document_protocol') != 2 or documents + extra >= config.document_slots))
                    or (not document and job.config.get('result_format') == 'overlay-tiles-v1' and not runtime.get('overlay_tiles'))):
                continue
            eligible.append(peer_id)
        if not eligible:
            continue
        if fallback is None and steal_before is not None and node.id in eligible and stage.available_at <= steal_before:
            fallback = stage.id
        chosen = min(eligible, key=lambda peer_id: (
            (loads[peer_id][1] + planned[peer_id] + 1) / configs[peer_id].performance_weight,
            loads[peer_id][1] + planned[peer_id], peer_id != node.id, peer_id))
        planned[chosen] += 1
        if chosen == node.id:
            assigned.add(stage.id)
    # A slow preferred peer gets a head start, not ownership of queued work.
    return assigned or ({fallback} if fallback is not None else set())


def has_eligible_work(db, node, stages, *, min_priority=0, blocked_providers=None):
    """Queue existence only; callers that need capacity check it once themselves."""
    stages = set(node.capabilities) & set(stages)
    if not node.enabled or not stages:
        return False
    blocked = _blocked_providers(db, node, stages) if blocked_providers is None else blocked_providers
    eligible = _eligible_stages(node, stages, now(), blocked_providers=blocked)
    if min_priority:
        eligible = eligible.where(JobStage.service_priority >= min_priority)
    # Advisory only: claim retires missing local inputs. Never stat a backlog here.
    return bool(db.scalar(select(eligible.exists())))


def ready_page_priority(db, node, min_priority):
    return db.scalar(_eligible_stages(node, {'page'}, now()).where(JobStage.service_priority >= min_priority)
        .with_only_columns(JobStage.service_priority)
        .order_by(JobStage.service_priority.desc(), JobStage.available_at, JobStage.id)
        .limit(1))


def has_claimable_work(db, node_id, allowed_stages=None):
    """Check capacity and queue eligibility; only a pull performs placement."""
    with db.no_autoflush:
        node = db.get(ComputeNode, node_id)
        free, ordinary = slot_budget(db, node)
        return free > 0 and has_eligible_work(db, node, allowed_stages or node.capabilities,
            min_priority=int(not ordinary))


def next_control_delay(db, maximum=5):
    """Bound idle reconciliation by retry deadlines and shared supplier RPM."""
    at = now()
    stages = ('text', 'validate_upload')
    pending = (select(JobStage.available_at).join(Job, Job.id == JobStage.job_id)
        .where(JobStage.name.in_(stages), JobStage.status == 'ready', JobStage.available_at > at,
            Job.status.in_(['queued', 'running', 'validating_upload']),
            Job.cancel_requested.is_(False), Job.discard_output.is_(False)))
    first = db.scalar(pending.order_by(JobStage.available_at).limit(1))
    delay = min(maximum, max(0, (first - at).total_seconds())) if first else maximum
    providers = db.execute(select(TranslationProvider.id, TranslationProvider.requests_per_minute,
        func.max(JobStage.service_priority).label('priority')).join(Job,
        Job.config['text']['provider_id'].as_string() == TranslationProvider.id)
        .join(JobStage, JobStage.job_id == Job.id).where(TranslationProvider.enabled.is_(True),
            JobStage.name == 'text', JobStage.status == 'ready', JobStage.available_at <= at,
            Job.status.in_(['queued', 'running']), Job.cancel_requested.is_(False), Job.discard_output.is_(False))
        .group_by(TranslationProvider.id, TranslationProvider.requests_per_minute)).all()
    from .redis_state import AdmissionUnavailable, window
    try:
        for provider in providers:
            retry = window('provider', provider.id, request_limit(provider.requests_per_minute, provider.priority))['retry_after_seconds']
            if retry:
                delay = min(delay, retry)
    except AdmissionUnavailable:
        delay = min(delay, 1)
    return max(0, delay)


def claim_stage(db, node_id, allowed_stages=None, *, executor_id=None):
    leases = claim_batch(db, node_id, allowed_stages, limit=1, executor_id=executor_id)
    return leases[0] if leases else None


@dataclass(frozen=True)
class ClaimCandidates:
    node_id: str
    stages: tuple | None
    limit: int
    signatures: dict
    blocked_providers: tuple


def prepare_claim_candidates(db, node_id, allowed_stages=None, *, limit=CLAIM_BATCH_LIMIT):
    """A bounded, non-authoritative snapshot, usable before a receipt transaction."""
    limit = max(0, min(CLAIM_BATCH_LIMIT, limit))
    if db.new or db.dirty or db.deleted:
        # Take the node before flushing a caller's pending job; don't refresh dirty objects.
        with db.no_autoflush:
            write_rows(db, select(ComputeNode.id).where(ComputeNode.id == node_id)).all()
    observed_node = db.get(ComputeNode, node_id)
    free, ordinary = slot_budget(db, observed_node) if limit else (0, 0)
    blocked = _blocked_providers(db, observed_node, allowed_stages) if free else ()
    candidates_before_lock = _preselect(db, observed_node, allowed_stages, blocked,
        int(not ordinary)) if free else {}
    return ClaimCandidates(node_id, tuple(sorted(allowed_stages)) if allowed_stages is not None else None,
        limit, candidates_before_lock, tuple(blocked))


def claim_batch(db, node_id, allowed_stages=None, *, limit=CLAIM_BATCH_LIMIT, executor_id=None, prepared=None):
    """Elect before locking the shared capacity row; the caller owns commit/rollback."""
    limit = max(0, min(CLAIM_BATCH_LIMIT, limit))
    if not limit:
        return []
    prepared = prepared or prepare_claim_candidates(db, node_id, allowed_stages, limit=limit)
    stages = tuple(sorted(allowed_stages)) if allowed_stages is not None else None
    if prepared.node_id != node_id or prepared.stages != stages:
        raise ValueError('Claim snapshot scope does not match the claimant')
    # report_pools supplies these workers' heartbeat independently of empty polls.
    if not prepared.signatures and node_id in ('control-text', 'control-validate_upload'):
        return []
    return claim_prepared(db, locked_node(db, node_id), prepared, min(limit, prepared.limit), executor_id=executor_id)


def claim_prepared(db, node, prepared, limit, *, executor_id=None, steal_before=None, higher_work=None):
    """Caller holds the node across capacity accounting, leasing and any pull receipt."""
    if not node or not node.enabled:
        return []
    candidates_before_lock, blocked, stages = prepared.signatures, prepared.blocked_providers, prepared.stages
    if node.engine_version != 'control':
        node.heartbeat_at = now()
    if not candidates_before_lock:
        return []
    free, ordinary = slot_budget(db, node)
    rank = next(iter(candidates_before_lock.values()))[1]
    limit = min(limit, free if rank else ordinary)
    if not limit:
        return []
    lock_jobs(db, select(JobStage.job_id).where(JobStage.id.in_(candidates_before_lock)))
    at = now()
    if not rank:
        # A paid stage may have arrived after preselection. Yield without a receipt.
        if (has_eligible_work(db, node, stages or node.capabilities, min_priority=1, blocked_providers=blocked)
                or (higher_work and higher_work(db, node, min_priority=1))):
            return []
    leases = []
    candidates = _claimable_candidates(db, node, stages, at, candidates_before_lock, blocked)
    pages = [(stage, job) for stage, job in candidates if stage.name == 'page']
    assigned = assigned_candidates(node, pages, compute_loads(db, node, at), steal_before=steal_before) if pages else set()
    for stage, job in candidates:
        if len(leases) == limit:
            break
        if stage.name == 'page' and stage.id not in assigned:
            continue
        leases.append(start_lease(db, node, stage, job, at, executor_id))
    return leases


def _claimable_candidates(db, node, allowed_stages, at, candidates_before_lock, blocked):
    stages = set(node.capabilities) & set(allowed_stages or node.capabilities)
    # Recheck only the bounded head after acquiring its job rows.
    candidates = [(stage, job) for stage, job in _candidate_rows(db, node, stages, at,
        stage_ids=candidates_before_lock, blocked_providers=blocked)
        if (stage.generation, stage.service_priority) == candidates_before_lock[stage.id]]
    if not candidates:
        return []
    sources = {job.input_asset_id for stage, job in candidates if stage.name != "validate_upload"}
    if not sources:
        return candidates
    assets = {row.id: row for row in db.scalars(select(Asset).where(Asset.id.in_(sources)).execution_options(populate_existing=True))}
    available_sources = {asset_id: available(asset) for asset_id, asset in assets.items()}
    failed_jobs = set()
    for stage, job in candidates:
        source = assets.get(job.input_asset_id)
        if (stage.name != "validate_upload" and source
                and not available_sources[source.id] and job.id not in failed_jobs):
            # Retire a missing head once so the next election can reach later pages.
            from .uploads import pause_missing_input
            pause_missing_input(db, job)
            failed_jobs.add(job.id)
    return [(stage, job) for stage, job in candidates if job.id not in failed_jobs
        and (stage.name == "validate_upload" or available_sources.get(job.input_asset_id, False))]


def start_lease(db, node, stage, job, at, executor_id=None):
    cfg = settings()
    if not job.attempt_id:
        job.attempt_id = uid()
        db.add(Attempt(id=job.attempt_id, job_id=job.id, provider_id=job.config["provider"]["id"],
                       lease_expires_at=at + timedelta(seconds=cfg.cluster_lease_seconds)))
    job.status, job.phase = "running", "analyze" if stage.name == "page" else stage.name
    stage.status, stage.generation, stage.attempts = "running", stage.generation + 1, stage.attempts + 1
    lease = ExecutionLease(id=uid(), stage_id=stage.id, job_id=job.id, node_id=node.id, owner_id=job.owner_id,
        executor_id=executor_id or (node.id if node.engine_version != "control" else None),
        generation=stage.generation, resource_pool=pool_for(stage), mode=job.mode, expires_at=at + timedelta(seconds=cfg.cluster_lease_seconds))
    if stage.name == 'page':
        from .node_config import NodeConfig
        config = NodeConfig.model_validate(node.desired_config)
        lease.limits = {'deadline_at': (at + timedelta(seconds=config.page_seconds)).isoformat() + 'Z',
                        'text_wait_seconds': config.text_wait_seconds, 'delivery_seconds': config.delivery_seconds,
                        'max_attempts': job.config.get('stage_attempts', cfg.cluster_stage_attempts)}
        lease.expires_at = min(lease.expires_at, at + timedelta(seconds=config.page_seconds))
    db.add(lease)
    touch_job(db, job)
    db.flush()
    return lease


def release_lease(db, lease, outcome):
    if lease.completed_at:
        return
    at = now()
    stage = db.get(JobStage, lease.stage_id)
    lease.completed_at, lease.outcome = at, outcome
    if stage.name == 'page':
        lease.limits = {**lease.limits, 'receipt': {'lease_id': lease.id, 'status': 'terminal',
            'outcome': outcome, 'result_hash': lease.result_hash, 'completed_at': at.isoformat() + 'Z',
            'job_status': db.get(Job, lease.job_id).status}}


def heartbeat_lease(db, lease_id, token):
    lock_lease_jobs(db, [lease_id])
    lease, stage, job = current_lease(db, lease_id, token)
    renew_lease(lease, db.get(Attempt, job.attempt_id))
    return lease


def renew_lease(lease, attempt):
    at = now()
    # The input file check can take the lease past its expiry.
    if lease.expires_at <= at:
        raise ProcessingError("LEASE_EXPIRED", "执行授权已失效")
    lease.expires_at = at + timedelta(seconds=settings().cluster_lease_seconds)
    attempt.heartbeat_at, attempt.lease_expires_at = at, lease.expires_at
