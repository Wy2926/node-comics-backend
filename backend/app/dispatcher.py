"""Lease reconciliation and retention maintenance. No message broker or dispatch backlog."""
from datetime import timedelta
import logging
import signal
from threading import Event
import time
from sqlalchemy import and_, or_, select
from .assets import available, asset_storage_key
from .config import settings
from .db import session_factory
from .runtime import check_runtime
from .errors import ProcessingError
from .health import log_failure, probe_oidc, report_failure, report_progress
from .models import Asset, Job, now
from .queue_models import ExecutionLease, JobStage
from .redis_state import AdmissionUnavailable
from .scheduler import lock_jobs, lock_scheduler
from .storage import get_store, StorageError
from .uploads import expire_uploads, recover_received_uploads
from .workers import fail_stage

def recover_lease(lease_id):
    with session_factory()() as db:
        lease = db.get(ExecutionLease, lease_id)
        if not lease or lease.completed_at or lease.expires_at > now():
            return
        document_lease = db.get(Job, lease.job_id).workflow_kind == 'document'
        if not document_lease and lease.result_hash and db.get(JobStage, lease.stage_id).name == 'page':
            from .compute_v3 import recover_compute_result
            if recover_compute_result(db, lease):
                db.commit()
                return
    if document_lease:
        from .documents.workflow import fail_lease
        fail_lease(lease_id, ProcessingError('WORKER_LEASE_EXPIRED', '文件节点失联，恢复可重复阶段'), recovering=True)
        return
    fail_stage(lease_id, ProcessingError('WORKER_LEASE_EXPIRED', '计算节点失联，保留检查点并恢复可重复阶段'), recovering=True)



_input_scan_cursor = None


def recover_missing_inputs(limit=100):
    """Queue loss is recoverable even when no compute node can claim the page."""
    global _input_scan_cursor
    with session_factory()() as db:
        candidates = list(db.scalars(select(Job.id).join(Asset, Asset.id == Job.input_asset_id).where(
            Job.status == 'queued', Job.cancel_requested.is_(False), Job.discard_output.is_(False),
            Asset.deleted_at.is_(None),
            Job.id > _input_scan_cursor if _input_scan_cursor else True).order_by(Job.id).limit(limit)))
    _input_scan_cursor = candidates[-1] if len(candidates) == limit else None
    for job_id in candidates:
        with session_factory()() as db:
            job = db.get(Job, job_id)
            source = db.get(Asset, job.input_asset_id)
            if get_store().exists(source.storage_key):
                continue
            lock_jobs(db, [job_id])
            db.refresh(job)
            if (job.status != 'queued' or job.cancel_requested or job.discard_output
                    or available(db.get(Asset, job.input_asset_id, populate_existing=True))):
                continue
            if db.scalar(select(ExecutionLease.id).where(ExecutionLease.job_id == job_id,
                    ExecutionLease.completed_at.is_(None)).limit(1)):
                continue
            from .uploads import pause_missing_input
            pause_missing_input(db, job)
            db.commit()


def recover_once():
    recover_missing_inputs()
    try:
        recover_received_uploads()
    except AdmissionUnavailable as error:
        # Upload recovery needs ingress state; SQL recovery and cleanup do not.
        log_failure("upload-recovery", error)
    with session_factory()() as db:
        ids = list(db.scalars(select(ExecutionLease.id).where(ExecutionLease.completed_at.is_(None), ExecutionLease.expires_at <= now()).limit(100)))
    for lease_id in ids:
        try:
            recover_lease(lease_id)
        except (StorageError, ProcessingError) as error:
            log_failure("lease-recovery", error, lease_id=lease_id)
            continue
    with session_factory()() as db:
        expire_uploads(db)
        db.commit()



_lease_cleanup_cursor = None
_upload_cleanup_cursor = None


def cleanup(db):
    """Only durable tombstones/terminal intents authorize physical deletion."""
    from .assets import cleanup_inputs, cleanup_outputs
    from .scheduler import ACTIVE
    from .upload_models import UploadReservation
    global _lease_cleanup_cursor, _upload_cleanup_cursor
    lock_scheduler(db)
    orphan_cutoff = now() - timedelta(seconds=max(3600, settings().upload_body_timeout_seconds * 2))
    from .guests import expire_guests
    from .result_retention import expire_registered_results
    from .system_settings import get_request_limits
    expire_guests(db)
    expire_registered_results(db)
    orphan_results = list(db.scalars(select(Asset).where(
        Asset.kind != "original", Asset.deleted_at.is_(None), Asset.created_at <= orphan_cutoff,
        ~select(Job.id).where(Job.output_asset_id == Asset.id).exists()).limit(100)))
    for asset in orphan_results:
        asset.deleted_at = now()
    db.flush()
    input_cutoff = now() - timedelta(hours=get_request_limits(db).image_input_retention_hours)
    originals = list(db.scalars(select(Asset.id).outerjoin(Job, Job.input_asset_id == Asset.id).where(
        Asset.kind == 'original', Asset.purged_at.is_(None),
        or_(Asset.deleted_at.is_not(None), and_(Job.status.not_in(ACTIVE), Job.completed_at <= input_cutoff),
            and_(Job.id.is_(None), Asset.created_at <= orphan_cutoff))).distinct().limit(200)))
    deleted = list(db.scalars(select(Asset).where(Asset.kind != 'original', Asset.deleted_at.is_not(None),
        Asset.purged_at.is_(None)).limit(200)))
    # Revoked assets cannot regain an authorization; no new grant path exists.
    db.commit()
    cleanup_inputs(originals)
    cleanup_outputs(db, deleted)
    # Keep lease pointers as tombstones, including after unlink: a cancelled
    # request may still finish writing and the next pass must remove that file.
    cutoff = now() - timedelta(seconds=settings().upload_body_timeout_seconds + 60)
    rows = db.execute(select(ExecutionLease.id, ExecutionLease.output_key).join(JobStage, JobStage.id == ExecutionLease.stage_id)
        .join(Job, Job.id == ExecutionLease.job_id).outerjoin(Asset, Asset.id == ExecutionLease.id).where(
            JobStage.name == 'page',
            ExecutionLease.completed_at.is_not(None), ExecutionLease.completed_at <= cutoff,
            ExecutionLease.output_key.is_not(None), Asset.id.is_(None),
            ExecutionLease.id > _lease_cleanup_cursor if _lease_cleanup_cursor else True)
        .order_by(ExecutionLease.id).limit(100)).all()
    _lease_cleanup_cursor = rows[-1][0] if len(rows) == 100 else None
    abandoned = list(db.scalars(select(UploadReservation.id).join(Job, Job.id == UploadReservation.job_id).where(
        Job.status.not_in(ACTIVE), UploadReservation.asset_id.is_(None), Job.completed_at <= cutoff,
        UploadReservation.id > _upload_cleanup_cursor if _upload_cleanup_cursor else True)
        .order_by(UploadReservation.id).limit(100)))
    _upload_cleanup_cursor = abandoned[-1] if len(abandoned) == 100 else None
    db.commit()
    for key in [row[1] for row in rows] + [asset_storage_key(value, 'original') for value in abandoned]:
        try:
            get_store().delete(key)
        except OSError:
            pass
    # Parts are never results. A generous age bound exceeds every bounded I/O.
    import time as wallclock
    root = settings().storage_path.resolve()
    part_cutoff = wallclock.time() - max(3600, settings().upload_body_timeout_seconds * 2)
    for part in (root / 'staging').glob('*.part'):
        if not part.resolve().is_relative_to(root):
            continue
        try:
            if part.stat().st_mtime < part_cutoff:
                part.unlink(missing_ok=True)
        except OSError:
            pass


def main():
    stopping = Event()
    for name in (signal.SIGTERM, signal.SIGINT):
        signal.signal(name, lambda *_: stopping.set())
    check_runtime()
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    from threading import Thread
    from .billing_sync import run as billing_maintenance
    billing_thread = Thread(target=billing_maintenance, args=(stopping,))
    billing_thread.start()
    from .auth_profiles import run as sync_auth_profiles
    auth_thread = Thread(target=sync_auth_profiles, args=(stopping,))
    auth_thread.start()
    next_oidc_probe = 0
    next_campaign_scan = 0
    next_retention_scan = 0
    next_checkpoint_scan = 0
    checkpoint_cutoff = None
    next_cleanup = 0
    while not stopping.is_set():
        try:
            recover_once()
            # Physical retention is not lease recovery. Avoid scanning history
            # under the scheduler lock on every two-second recovery tick.
            if time.monotonic() >= next_cleanup:
                with session_factory()() as db:
                    cleanup(db)
                next_cleanup = time.monotonic() + 60
            if checkpoint_cutoff is not None or time.monotonic() >= next_checkpoint_scan:
                from .execution_retention import compact_checkpoints
                if checkpoint_cutoff is None:
                    checkpoint_cutoff = now()
                    next_checkpoint_scan = time.monotonic() + 6 * 3600
                # Drain one bounded transaction per recovery tick without chasing newly completed pages.
                with session_factory()() as db:
                    compacted = compact_checkpoints(db, at=checkpoint_cutoff)
                    db.commit()
                if not any(compacted.values()):
                    checkpoint_cutoff = None
            if time.monotonic() >= next_retention_scan:
                from .execution_retention import cleanup_execution_data
                with session_factory()() as db:
                    cleanup_execution_data(db)
                    db.commit()
                next_retention_scan = time.monotonic() + 10
            if time.monotonic() >= next_oidc_probe:
                probe_oidc()
                next_oidc_probe = time.monotonic() + max(1, min(60, settings().cluster_node_timeout_seconds / 3))
            if time.monotonic() >= next_campaign_scan:
                from .quota_campaigns import backfill_campaigns
                backfill_campaigns()
                next_campaign_scan = time.monotonic() + 10
            report_progress("maintenance")
        except Exception as error:
            report_failure("maintenance", error)
        stopping.wait(settings().dispatch_interval_seconds)
    billing_thread.join()
    auth_thread.join()


if __name__ == "__main__":
    main()
