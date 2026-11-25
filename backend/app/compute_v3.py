"""Whole-page compute leases. Image work stays on the node; text stays here."""
import asyncio
from datetime import datetime, timedelta
import hashlib
import hmac
import re
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, Header, Response, Request, HTTPException
from starlette.responses import StreamingResponse
from starlette.datastructures import UploadFile
from pydantic import Field, ValidationError, model_validator
from sqlalchemy import and_, case, select
from sqlalchemy.orm import aliased, load_only, Session

from .assets import available, asset_storage_key, create_asset
from .classic import validate_analysis
from .node_auth import node_auth
from .config import settings
from .db import get_db, session_factory
from .errors import ProcessingError, node_error, problem
from .languages import Language, LANGUAGES
from .translation_fonts import FontId, FONT_LANGUAGES, resolve_font, supports_font
from .models import Asset, Attempt, ClassicState, Job, now
from .node_config import NodeConfig
from .providers import digest
from .queue_models import ComputeNode, ExecutionLease, JobStage
from .request_models import RequestBody
from .scheduler import check_current_lease, current_lease, locked_node, lock_lease_jobs, release_lease, renew_lease, touch_job
from .storage import get_store
from .workers import fail_stage, finish_job


def no_store(response: Response):
    response.headers['Cache-Control'] = 'no-store'


router = APIRouter(prefix='/internal/compute/v3', tags=['compute v3'], dependencies=[Depends(no_store)])


def stamp(value):
    return value.isoformat() + 'Z'


def parsed(value):
    return datetime.fromisoformat(value.removesuffix('Z'))


def config_payload(node):
    config = NodeConfig.model_validate(node.desired_config)
    keys = ('execution_slots', 'document_slots', 'poll_seconds', 'heartbeat_seconds',
            'request_seconds', 'page_seconds', 'text_wait_seconds', 'delivery_seconds')
    return {'version': node.config_version, 'enabled': node.enabled,
            **{key: getattr(config, key) for key in keys}}


def scoped_node(db, identity, node_id=None):
    if node_id is not None and identity != node_id:
        problem('NODE_SCOPE_MISMATCH', '节点身份不匹配', 403)
    return locked_node(db, identity)


def scoped_lease(db, lease_id, token, identity, *, lock=False):
    if lock:
        lock_lease_jobs(db, [lease_id], identity)
    lease = db.get(ExecutionLease, lease_id, populate_existing=lock)
    if not lease or lease.node_id != identity or not hmac.compare_digest(lease.token.encode(), token.encode()):
        problem('NODE_SCOPE_MISMATCH', '租约未授权给此节点', 403)
    if db.get(JobStage, lease.stage_id).name != 'page':
        problem('PROTOCOL_MISMATCH', '此租约不属于整页协议', 409)
    return lease


def page_deadline(lease, accepted):
    limits = lease.limits
    deadline = parsed(limits['deadline_at'])
    if limits.get('delivery_deadline_at'):
        deadline = min(deadline, parsed(limits['delivery_deadline_at']))
    if accepted:
        deadline = min(deadline, parsed(accepted) + timedelta(seconds=limits['text_wait_seconds']))
    return deadline


def lease_deadline(db, lease):
    accepted = db.scalar(select(ClassicState.artifacts['analysis_accepted_at'].as_string())
        .join(JobStage, JobStage.job_id == ClassicState.job_id)
        .where(ClassicState.job_id == lease.job_id, JobStage.name == 'text', JobStage.status != 'succeeded'))
    return page_deadline(lease, accepted)


def check_page_input(asset, deadline):
    if not available(asset):
        raise ProcessingError('ASSET_EXPIRED', '原图访问已失效')
    if deadline <= now():
        raise ProcessingError('PAGE_DEADLINE_EXCEEDED', '整页任务已达到处理时限')


def live_page(db, lease_id):
    lease, stage, job = current_lease(db, lease_id)
    check_page_input(db.get(Asset, job.input_asset_id), lease_deadline(db, lease))
    return lease, stage, job


def node_lease_states(node_id):
    """The heartbeat and channel use the same fenced metadata; bodies are fetched only for changes."""
    text_stage = aliased(JobStage)
    return (select(ExecutionLease, JobStage, Job, Asset, text_stage.status.label('text_status'),
            text_stage.result['revision'].as_string().label('revision'),
            ClassicState.artifacts['analysis_accepted_at'].as_string().label('accepted'))
        .join(JobStage, JobStage.id == ExecutionLease.stage_id).join(Job, Job.id == ExecutionLease.job_id)
        .outerjoin(Asset, Asset.id == Job.input_asset_id)
        .outerjoin(text_stage, and_(text_stage.job_id == Job.id, text_stage.name == 'text', text_stage.page_index == 0))
        .outerjoin(ClassicState, and_(ClassicState.job_id == Job.id, text_stage.status != 'succeeded'))
        .where(ExecutionLease.node_id == node_id)
        .options(load_only(JobStage.name, JobStage.service_priority, JobStage.status, JobStage.generation),
                 load_only(Job.workflow_kind, Job.status, Job.cancel_requested, Job.discard_output),
                 load_only(Asset.deleted_at, Asset.purged_at, Asset.expires_at, Asset.active_references, Asset.storage_key))
        .execution_options(populate_existing=True))


def checked_page_deadline(row):
    lease = row.ExecutionLease
    check_current_lease(lease, row.JobStage, row.Job)
    deadline = page_deadline(lease, row.accepted)
    check_page_input(row.Asset, deadline)
    return deadline


def input_descriptor(db, lease, source):
    return {'path': f'/internal/compute/v3/leases/{lease.id}/input',
            'sha256': source.sha256, 'byte_size': source.byte_size, 'mime': source.mime,
            'width': source.width, 'height': source.height, 'normalization_version': source.normalization_version}


def translation_payloads(db, job_ids):
    if not job_ids:
        return {}
    # Only checkpoints without a frozen analysis hash need the OCR body.
    needs_analysis = JobStage.result['analysis_hash'].as_string().is_(None)
    query = (select(Job.id, JobStage.result, case((needs_analysis, ClassicState.analysis)).label('legacy_analysis'),
                    ClassicState.translations, Job.target_language).select_from(ClassicState)
        .join(Job, Job.id == ClassicState.job_id).join(JobStage, JobStage.job_id == Job.id)
        .where(Job.id.in_(job_ids), JobStage.name == 'text', JobStage.status == 'succeeded', ClassicState.retention_stage < 2))
    payloads = {}
    for row in db.execute(query):
        if row.result.get('analysis_hash') or row.legacy_analysis:
            result = {'analysis_hash': row.result.get('analysis_hash') or digest(row.legacy_analysis),
                      'language': row.target_language, 'translations': row.translations}
            payloads[row.id] = {**result, 'revision': row.result.get('revision') or digest(result)}
    return payloads


def translations_payload(db, job_id):
    return translation_payloads(db, [job_id]).get(job_id)


def changed_translations(db, pages):
    pending = [(target, row.ExecutionLease.job_id, known) for target, row, known in pages
               if row.text_status == 'succeeded' and (not row.revision or row.revision != known)]
    payloads = translation_payloads(db, {job_id for _, job_id, _ in pending})
    for target, job_id, known in pending:
        translated = payloads.get(job_id)
        if translated and translated['revision'] != known:
            yield target, translated


def receipt(lease):
    return (lease.limits or {}).get('receipt') or {
        'lease_id': lease.id, 'status': 'terminal', 'outcome': lease.outcome,
        'result_hash': lease.result_hash, 'completed_at': stamp(lease.completed_at)}


def lease_payload(db, lease):
    if lease.completed_at:
        return receipt(lease)
    try:
        _, stage, job = live_page(db, lease.id)
    except ProcessingError as error:
        return {'lease_id': lease.id, 'lease_token': lease.token, 'status': 'stop', 'code': error.code}
    source = db.get(Asset, job.input_asset_id)
    analysis = db.scalar(select(ClassicState.analysis).where(ClassicState.job_id == job.id))
    return {'lease_id': lease.id, 'lease_token': lease.token, 'job_id': job.id, 'generation': lease.generation,
            'status': 'active', 'expires_at': stamp(lease.expires_at), 'limits': lease.limits,
            'input': input_descriptor(db, lease, source), 'config': {'engine': {'protocol_version': 3},
                'font_id': resolve_font(job.config.get('font_id'), job.target_language),
                **({'result_format': 'overlay-tiles-v1'} if job.config.get('result_format') == 'overlay-tiles-v1' else {})},
            'language': job.target_language, 'analysis': analysis,
            'analysis_hash': ((stage.result or {}).get('analysis_hash') or digest(analysis)) if analysis else None,
            'translations': translations_payload(db, job.id)}


class Registration(RequestBody):
    protocol_version: Literal[3]
    engine_version: str = Field(min_length=1, max_length=120)
    resource_id: str = Field(min_length=1, max_length=120)
    device: str = Field(min_length=1, max_length=80)
    supported_languages: list[Language] = Field(min_length=1, max_length=len(LANGUAGES))
    ready: bool
    document_protocol: Literal[2] | None = None
    result_formats: list[Literal['overlay-v1', 'overlay-tiles-v1']] = Field(default_factory=lambda: ['overlay-v1'], max_length=2)
    supported_font_ids: list[FontId] = Field(default_factory=list, max_length=len(FONT_LANGUAGES))


@router.post('/nodes/register')
def register(body: Registration, identity=Depends(node_auth), db: Session = Depends(get_db)):
    node = scoped_node(db, identity)
    if body.resource_id != node.resource_id or body.engine_version == 'control':
        problem('NODE_RESOURCE_MISMATCH', '节点资源或引擎身份不匹配', 409)
    leased = db.execute(select(ExecutionLease, Job.config['font_id'].as_string(), Job.target_language)
        .join(JobStage, JobStage.id == ExecutionLease.stage_id).join(Job, Job.id == ExecutionLease.job_id).where(
            ExecutionLease.node_id == identity, ExecutionLease.completed_at.is_(None), JobStage.name == 'page')).all()
    # A downgraded runtime must not resume pages or owned document workspaces
    # without their frozen font (or the effective default of historical jobs).
    from .documents.models import Document
    unsupported_document = db.scalar(select(Job.id).join(Document, Document.job_id == Job.id).where(
        Document.owner_node_id == identity, Job.status.not_in(('succeeded', 'failed', 'cancelled')),
        ~supports_font(Job.config['font_id'].as_string(), Job.target_language, body.supported_font_ids)).limit(1))
    if unsupported_document or any(lease.expires_at > now() and resolve_font(font_id, language) not in body.supported_font_ids
                                  for lease, font_id, language in leased):
        problem('NODE_FONT_CAPABILITY_CONFLICT', '节点仍有未完成的字体任务，请保留其字体支持或等待任务恢复', 409)
    leases = [lease for lease, _, _ in leased]
    node.engine_version, node.device = body.engine_version, body.device
    node.capabilities = ['page'] if body.ready else []
    if body.ready and body.document_protocol == 2:
        node.capabilities += ['doc_prepare', 'doc_ocr', 'doc_render', 'doc_zip']
    node.supported_languages = list(dict.fromkeys(body.supported_languages))
    node.runtime_report = {'protocol_version': 3, 'ready': body.ready, 'languages': body.supported_languages,
        'supported_font_ids': list(dict.fromkeys(body.supported_font_ids)) if body.ready else [],
        'overlay_tiles': body.ready and 'overlay-tiles-v1' in body.result_formats,
        'document_protocol': body.document_protocol if body.ready else None}
    node.applied_config_version, node.config_error, node.heartbeat_at = node.config_version, None, now()
    result = {'protocol_version': 3, 'config': config_payload(node), 'server_time': stamp(now()),
              'leases': [lease_payload(db, lease) for lease in leases]}
    db.commit()
    return result


class LeaseRequest(RequestBody):
    lease_token: str = Field(min_length=1, max_length=64)


class HeartbeatItem(LeaseRequest):
    lease_id: str = Field(min_length=1, max_length=36)
    translations_revision: str | None = Field(default=None, max_length=64)
    phase: Literal['queued', 'download', 'analyze', 'inpaint', 'text', 'render', 'deliver', 'stopped'] = 'queued'


class HeartbeatRequest(RequestBody):
    config_version: int = Field(ge=1, strict=True)
    leases: list[HeartbeatItem] = Field(max_length=32)


@router.post('/nodes/{node_id}/heartbeat')
def heartbeat(node_id: str, body: HeartbeatRequest, identity=Depends(node_auth), db: Session = Depends(get_db)):
    node = scoped_node(db, identity, node_id)
    node.heartbeat_at = now()
    states = {}
    if body.leases:
        keys = {item.lease_id for item in body.leases}
        lock_lease_jobs(db, keys, identity)
        query = (node_lease_states(identity).where(JobStage.name == 'page', ExecutionLease.id.in_(keys))
            .add_columns(Attempt).outerjoin(Attempt, Attempt.id == Job.attempt_id).options(load_only(Attempt.id)))
        states = {row.ExecutionLease.id: row for row in db.execute(query)}
    replies = []
    pending = []
    for item in body.leases:
        row = states.get(item.lease_id)
        if row is None or not hmac.compare_digest(row.ExecutionLease.token.encode(), item.lease_token.encode()):
            replies.append({'lease_id': item.lease_id, 'status': 'stop', 'code': 'NODE_SCOPE_MISMATCH'})
            continue
        lease = row.ExecutionLease
        if lease.completed_at:
            replies.append(receipt(lease))
            continue
        try:
            deadline = checked_page_deadline(row)
            renew_lease(lease, row.Attempt)
            lease.expires_at = (parsed(lease.limits['delivery_persist_deadline_at'])
                if accepted_delivery(lease) else min(lease.expires_at, deadline))
        except ProcessingError as error:
            # One rejected item must not roll back unrelated renewals.
            replies.append({'lease_id': lease.id, 'status': 'stop', 'code': error.code})
            continue
        reply = {'lease_id': lease.id, 'status': 'active', 'expires_at': stamp(lease.expires_at)}
        pending.append((reply, row, item.translations_revision))
        replies.append(reply)
    for reply, translated in changed_translations(db, pending):
        reply['translations'] = translated
    result = {'server_time': stamp(now()), 'leases': replies, 'config': config_payload(node)}
    db.commit()
    return result


class AnalysisRequest(LeaseRequest):
    analysis_hash: str = Field(pattern=r'^[a-f0-9]{64}$')
    analysis: dict


@router.post('/leases/{lease_id}/analysis')
def analysis(lease_id: str, body: AnalysisRequest, identity=Depends(node_auth), db: Session = Depends(get_db)):
    lease = scoped_lease(db, lease_id, body.lease_token, identity)
    job = db.get(Job, lease.job_id)
    source = db.get(Asset, job.input_asset_id)
    if digest(body.analysis) != body.analysis_hash:
        problem('ANALYSIS_HASH_MISMATCH', '分析摘要不匹配', 422)
    if body.analysis.get('input_hash') != source.sha256:
        problem('ENGINE_RESULT_MISMATCH', '分析与输入不一致', 422)
    validate_analysis(body.analysis, source.width, source.height)  # Decode outside scheduler locks.
    db.rollback()
    lease = scoped_lease(db, lease_id, body.lease_token, identity, lock=True)
    stage = db.get(JobStage, lease.stage_id)
    saved_hash = (stage.result or {}).get('analysis_hash')
    # Terminal replay uses the durable digest, not a checkpoint that may be reclaimed.
    if lease.completed_at and lease.outcome == 'succeeded' and saved_hash == body.analysis_hash:
        return {'analysis_hash': body.analysis_hash, 'receipt': receipt(lease)}
    _, _, job = live_page(db, lease_id)
    state = db.get(ClassicState, lease.job_id) if not saved_hash else None
    if saved_hash or state.analysis:
        if (saved_hash or digest(state.analysis)) != body.analysis_hash:
            problem('ANALYSIS_CONFLICT', '已持久化的分析不可替换', 409)
    else:
        state.analysis, state.timings = body.analysis, body.analysis.get('timings', {})
        state.artifacts = {'analysis_accepted_at': stamp(now())}
        stage.result = {'analysis_hash': body.analysis_hash, 'analysis_generation': lease.generation}
        if not body.analysis['segments']:
            finish_job(db, job, 'no_text')
            stage.status, stage.completed_at = 'succeeded', now()
            lease.result_hash = body.analysis_hash
            release_lease(db, lease, 'succeeded')
        else:
            text = db.scalar(select(JobStage).where(JobStage.job_id == job.id, JobStage.name == 'text'))
            text.status, text.available_at = 'ready', now()
            job.phase = 'text'
            touch_job(db, job)
    result = {'analysis_hash': body.analysis_hash, 'receipt': receipt(lease) if lease.completed_at else None}
    db.commit()
    return result


@router.get('/leases/{lease_id}/input', dependencies=[Depends(node_auth)], responses={
    206: {'description': 'Single byte range of the immutable input, with Content-Range.'},
    416: {'description': 'Invalid or unsatisfiable single byte range, with Content-Range: bytes */size.'}})
def read_input(lease_id: str, db: Session = Depends(get_db),
               range_header: Annotated[str | None, Header(alias='Range')] = None,
               if_range: Annotated[str | None, Header(alias='If-Range')] = None):
    # Trusted nodes read existing files; execution fencing stays on control routes.
    source = db.execute(select(Asset.storage_key, Asset.mime, Asset.byte_size, Asset.sha256)
        .join(Job, Job.input_asset_id == Asset.id)
        .join(ExecutionLease, ExecutionLease.job_id == Job.id)
        .where(ExecutionLease.id == lease_id)).one_or_none()
    db.rollback()  # Release the read transaction before opening or streaming the file.
    if source is None:
        raise ProcessingError('INPUT_UNAVAILABLE', '临时原图不可用，请恢复输入')
    size = source.byte_size
    etag = f'"{source.sha256}"'
    headers = {'Accept-Ranges': 'bytes', 'ETag': etag, 'Cache-Control': 'private, no-store'}
    start, end, status = 0, size - 1, 200
    if range_header is not None and (if_range is None or if_range == etag):
        # Only one range is useful for sequential resume; never build multipart ranges.
        match = re.fullmatch(r'bytes=([0-9]{0,20})-([0-9]{0,20})', range_header)
        if match and any(match.groups()):
            first, last = match.groups()
            if first:
                start = int(first)
                end = min(int(last), end) if last else end
            else:
                start = max(0, size - int(last))
        else:
            start = size
        if not 0 <= start <= end < size:
            return Response(status_code=416, headers={**headers, 'Content-Range': f'bytes */{size}'})
        status = 206
        headers['Content-Range'] = f'bytes {start}-{end}/{size}'
    headers['Content-Length'] = str(end - start + 1)
    try:
        handle = get_store().path(source.storage_key).open('rb')
    except OSError:
        raise ProcessingError('INPUT_UNAVAILABLE', '临时原图不可用，请恢复输入') from None

    def chunks():
        try:
            handle.seek(start)
            remaining = end - start + 1
            while remaining and (chunk := handle.read(min(256 * 1024, remaining))):
                remaining -= len(chunk)
                yield chunk
        finally:
            handle.close()
    return StreamingResponse(chunks(), status_code=status, media_type=source.mime, headers=headers)


class NodeError(RequestBody):
    # Only codes with scheduling semantics are interpreted by the controller.
    # Other node versions may add diagnostic codes without a center release.
    code: str = Field(min_length=1, max_length=60, pattern=r'^[A-Z][A-Z0-9_]*$')


class OutputInfo(RequestBody):
    sha256: str = Field(pattern=r'^[a-f0-9]{64}$')
    byte_size: int = Field(ge=1, le=128 * 1024 * 1024, strict=True)
    width: int = Field(ge=1, strict=True)
    height: int = Field(ge=1, strict=True)
    mime: Literal['image/webp', 'application/vnd.nodelane.overlay-tiles']


class BBox(RequestBody):
    x: int = Field(ge=0, strict=True)
    y: int = Field(ge=0, strict=True)
    width: int = Field(ge=1, strict=True)
    height: int = Field(ge=1, strict=True)


class PageResult(RequestBody):
    version: str = Field(min_length=1, max_length=120)
    input_hash: str = Field(pattern=r'^[a-f0-9]{64}$')
    analysis_hash: str = Field(pattern=r'^[a-f0-9]{64}$')
    translations_revision: str = Field(pattern=r'^[a-f0-9]{64}$')
    representation: Literal['overlay-v1', 'overlay-tiles-v1', 'original']
    normalization_version: Literal[1]
    # validate_result binds these to the already admitted input dimensions;
    # shape binds the output to this canvas. No second hard-coded ceiling.
    width: int = Field(ge=1, strict=True)
    height: int = Field(ge=1, strict=True)
    bbox: BBox | None
    output: OutputInfo | None

    @model_validator(mode='after')
    def shape(self):
        if self.representation == 'original':
            if self.bbox is not None or self.output is not None:
                raise ValueError('Original representation must not have an artifact')
        elif self.representation == 'overlay-tiles-v1':
            if (self.bbox is not None or self.output is None or self.output.mime != 'application/vnd.nodelane.overlay-tiles'
                    or (self.output.width, self.output.height) != (self.width, self.height)):
                raise ValueError('Invalid tile representation')
        elif (self.bbox is None or self.output is None or self.output.mime != 'image/webp'
              or self.bbox.x + self.bbox.width > self.width
              or self.bbox.y + self.bbox.height > self.height
              or (self.bbox.width, self.bbox.height) != (self.output.width, self.output.height)):
            raise ValueError('Overlay bounds must match the artifact and input canvas')
        return self


Timings = dict[Literal['download', 'download_queue', 'analyze', 'analyze_queue', 'inpaint',
    'inpaint_queue', 'render', 'render_queue', 'analysis_submit', 'text_wait', 'local_total', 'output_put',
    'render_areas', 'render_layout', 'render_diff', 'render_encode',
    'detect_lock_wait', 'ocr_lock_wait', 'inpaint_lock_wait'],
    Annotated[float, Field(ge=0, le=86400, allow_inf_nan=False)]]


class OutputRequest(LeaseRequest):
    result: PageResult
    timings: Timings = Field(default_factory=dict)


def result_multipart_schema():
    """Describe the JSON string without duplicating its validation model."""
    schema = OutputRequest.model_json_schema()
    definitions = schema.pop('$defs', {})

    def inline(value):
        if isinstance(value, list):
            return [inline(item) for item in value]
        if isinstance(value, dict):
            if '$ref' in value:
                return inline(definitions[value['$ref'].removeprefix('#/$defs/')])
            return {key: inline(item) for key, item in value.items()}
        return value

    return {'type': 'object', 'additionalProperties': False, 'required': ['metadata'], 'properties': {
        'metadata': {'type': 'string', 'contentMediaType': 'application/json', 'contentSchema': inline(schema),
            'description': 'JSON-encoded OutputRequest: lease_token, result and optional timings; at most 64 KiB.'},
        'output': {'type': 'string', 'format': 'binary',
            'description': 'Lossless WebP overlay or negotiated WebP tile bundle; required for overlay-v1/overlay-tiles-v1 and forbidden for original.'}}}


def validate_result(db, job, result):
    source = db.get(Asset, job.input_asset_id)
    if result['representation'] == 'overlay-tiles-v1' and job.config.get('result_format') != 'overlay-tiles-v1':
        problem('INVALID_PROVIDER_OUTPUT', '任务未协商分块覆盖格式', 422)
    if (result['input_hash'] != source.sha256 or result['normalization_version'] != source.normalization_version):
        problem('ENGINE_RESULT_MISMATCH', '结果与输入不一致', 409)
    if ((result['width'], result['height']) != (source.width, source.height)
            or max(result['width'], result['height']) > settings().max_dimension
            or result['output'] and result['output']['byte_size'] > settings().cluster_max_result_bytes):
        problem('INVALID_PROVIDER_OUTPUT', '结果元数据超出图片限制', 422)
    frozen = db.scalar(select(JobStage.result).join(ClassicState, ClassicState.job_id == JobStage.job_id).where(
        JobStage.job_id == job.id, JobStage.name == 'text', JobStage.page_index == 0,
        JobStage.status == 'succeeded', ClassicState.retention_stage < 2))
    if frozen is not None and (not frozen.get('revision') or not frozen.get('analysis_hash')):
        frozen = translations_payload(db, job.id)
    if not frozen or (result['analysis_hash'], result['translations_revision']) != (frozen['analysis_hash'], frozen['revision']):
        problem('TRANSLATIONS_MISMATCH', '结果未绑定当前分析与译文版本', 409)


def verify_output(handle, result):
    info = result['output']
    if info is None:
        if handle is not None:
            problem('INVALID_PROVIDER_OUTPUT', '原图表示不能包含文件', 422)
        return
    if handle is None:
        problem('INVALID_PROVIDER_OUTPUT', '缺少覆盖文件', 422)
    handle.seek(0)
    sha, size = hashlib.sha256(), 0
    while chunk := handle.read(64 * 1024):
        size += len(chunk)
        if size > min(info['byte_size'], settings().cluster_max_result_bytes):
            problem('INVALID_PROVIDER_OUTPUT', '结果长度超过声明或限制', 422)
        sha.update(chunk)
    if size != info['byte_size'] or sha.hexdigest() != info['sha256']:
        problem('RESULT_HASH_MISMATCH', '结果字节与冻结摘要不符', 422)
    handle.seek(0)
    try:
        if result['representation'] == 'overlay-tiles-v1':
            from .overlay_tiles import verify_tiles
            verify_tiles(handle, result)
            handle.seek(0)
            return
        from .image_metadata import image_metadata
        metadata = image_metadata(handle)
        if metadata['mime'] != 'image/webp' or (metadata['width'], metadata['height']) != (info['width'], info['height']):
            raise ValueError('Invalid overlay container')
    except (OSError, ValueError, TypeError, KeyError):
        problem('INVALID_PROVIDER_OUTPUT', '覆盖文件容器或元数据无效', 422)
    handle.seek(0)


def commit_result(db, lease, stage, job, result, timings):
    """Only called with the job locked and verified durable artifact bytes."""
    validate_result(db, job, result)
    if result['output'] is not None:
        output = create_asset(db, job.owner_id, b'', kind='classic', parent_id=job.input_asset_id,
            stable_id=lease.id, prewritten=True, verified_info=result['output'],
            representation=result['representation'], bbox=result['bbox'], normalization_version=1,
            storage_key=lease.output_key)
        job.output_asset_id = output.id
    job.result_description = result
    state, flags = db.execute(select(ClassicState, ClassicState.analysis['quality_flags'])
        .where(ClassicState.job_id == job.id).options(load_only(ClassicState.job_id))).one()
    job.quality_flags = flags or []
    stage.status, stage.completed_at = 'succeeded', now()
    finish_job(db, job, 'succeeded')
    release_lease(db, lease, 'succeeded')
    state.timings = {'node': timings, 'delivery': {'protocol': 3}}
    return receipt(lease)


def accepted_delivery(lease):
    """Only a fully verified body received before its frozen cutoff may publish.

    This receipt is committed before writing the immutable artifact. Disk fsync
    and the final SQL transaction may finish later, without making a late body
    valid or leaving a file-before-receipt crash window.
    """
    limits = lease.limits or {}
    try:
        received = parsed(limits['delivery_received_at'])
        cutoff = min(parsed(limits['delivery_accept_deadline_at']), parsed(limits['deadline_at']),
                     parsed(limits['delivery_deadline_at']))
        return received < cutoff
    except (KeyError, TypeError, ValueError):
        return False


def delivery_target(db, lease):
    stage = db.get(JobStage, lease.stage_id, populate_existing=True)
    job = db.get(Job, lease.job_id, populate_existing=True)
    if (lease.completed_at or not stage or stage.generation != lease.generation or stage.status != 'running'
            or not job or job.status != 'running' or job.cancel_requested or job.discard_output):
        raise ProcessingError('LEASE_EXPIRED', '任务已停止或执行代次已更新')
    if not accepted_delivery(lease):
        raise ProcessingError('PAGE_DEADLINE_EXCEEDED', '结果未在处理时限内完成接收')
    return stage, job


def receive_result(lease_id, body, handle, identity):
    result = body.result.model_dump()
    result_hash = digest(result)
    with session_factory()() as db:
        lease = scoped_lease(db, lease_id, body.lease_token, identity, lock=True)
        if lease.completed_at:
            if lease.result_hash == result_hash:
                return receipt(lease)
            problem('COMPLETION_CONFLICT', '租约已结束或交付另一份结果', 409)
        lease, _, job = live_page(db, lease_id)
        validate_result(db, job, result)
        if lease.result_hash is not None and lease.result_hash != result_hash:
            problem('COMPLETION_CONFLICT', '交付摘要已冻结', 409)
        if 'delivery_deadline_at' not in lease.limits:
            lease.limits = {**lease.limits,
                'delivery_deadline_at': stamp(now() + timedelta(seconds=lease.limits['delivery_seconds']))}
        lease.result_hash = result_hash
        lease.output_key = asset_storage_key(lease.id, 'classic') if result['output'] else None
        lease.limits = {**lease.limits, 'delivery_result': result, 'delivery_timings': body.timings}
        key = lease.output_key
        db.commit()  # Intent precedes bytes; network/file I/O never holds this lock.
    verify_output(handle, result)
    with session_factory()() as db:
        lease = scoped_lease(db, lease_id, body.lease_token, identity, lock=True)
        if lease.completed_at:
            if lease.result_hash == result_hash:
                return receipt(lease)
            problem('COMPLETION_CONFLICT', '租约已结束', 409)
        lease, _, job = current_lease(db, lease_id)
        deadline = lease_deadline(db, lease)
        check_page_input(db.get(Asset, job.input_asset_id), deadline)
        accepted_at = now()
        cutoff = min(lease.expires_at, deadline)
        if accepted_at >= cutoff:
            raise ProcessingError('PAGE_DEADLINE_EXCEEDED', '结果未在处理时限内完成接收')
        if not lease.limits.get('delivery_received_at'):
            persist_deadline = max(lease.expires_at,
                accepted_at + timedelta(seconds=settings().upload_body_timeout_seconds))
            lease.limits = {**lease.limits, 'delivery_received_at': stamp(accepted_at),
                            'delivery_accept_deadline_at': stamp(cutoff),
                            'delivery_persist_deadline_at': stamp(persist_deadline)}
            lease.expires_at = persist_deadline
        db.commit()
    if key is not None:
        get_store().put_file(key, handle, result['output']['mime'], kind='classic')
    with session_factory()() as db:
        lease = scoped_lease(db, lease_id, body.lease_token, identity, lock=True)
        if lease.completed_at:
            if lease.result_hash == result_hash:
                return receipt(lease)
            problem('COMPLETION_CONFLICT', '租约已结束', 409)
        stage, job = delivery_target(db, lease)
        reply = commit_result(db, lease, stage, job, result, body.timings)
        db.commit()
        return reply


@router.put('/leases/{lease_id}/result', openapi_extra={
    'requestBody': {'required': True, 'content': {'multipart/form-data': {
        'schema': result_multipart_schema(), 'encoding': {
            'metadata': {'contentType': 'application/json'},
            'output': {'contentType': 'image/webp, application/vnd.nodelane.overlay-tiles'}}}}},
    'responses': {
        '200': {'description': 'Stable terminal receipt; replaying the same result never settles twice.'},
        '408': {'description': 'Multipart total body timeout; retry the same frozen result.'},
        '409': {'description': 'Lease or generation is no longer active, or the frozen result conflicts.'},
        '422': {'description': 'Invalid metadata, container structure, file hash/size/dimensions, or processing deadline exceeded.'},
        '503': {'description': 'Result ingress capacity or storage is unavailable; retry the same frozen result.'}}})
async def deliver_result(lease_id: str, request: Request, identity=Depends(node_auth)):
    from .result_ingress import begin_result_ingress, release_result_ingress
    from .storage import StorageError
    from .upload_ingress import finish_thread
    try:
        slot = await begin_result_ingress()
    except StorageError as error:
        raise HTTPException(503, detail={'code': error.code, 'message': '结果接收暂时繁忙，请重试'},
                            headers={'Retry-After': '2'}) from None
    form = None
    try:
        try:
            async with asyncio.timeout(settings().upload_body_timeout_seconds):
                form = await request.form(max_files=1, max_fields=1, max_part_size=65536)
        except TimeoutError:
            problem('UPLOAD_TIMEOUT', '结果接收超时，请重试同一结果', 408)
        if set(form) - {'metadata', 'output'} or len(form.getlist('metadata')) != 1 or len(form.getlist('output')) > 1:
            problem('INVALID_COMPLETION', '结果请求只能包含 metadata 与一个 output', 422)
        raw = form.get('metadata')
        if not isinstance(raw, str) or len(raw.encode('utf-8')) > 65536:
            problem('INVALID_COMPLETION', '结果描述无效或超过限制', 422)
        try:
            body = OutputRequest.model_validate_json(raw)
        except ValidationError:
            problem('INVALID_COMPLETION', '结果描述字段无效', 422)
        upload = form.get('output')
        if upload is not None and not isinstance(upload, UploadFile):
            problem('INVALID_COMPLETION', 'output 必须为二进制文件', 422)
        return await finish_thread(receive_result, lease_id, body, upload.file if upload else None, identity)
    finally:
        try:
            if form is not None:
                await form.close()
        finally:
            await finish_thread(release_result_ingress, slot)


def recover_compute_result(db, lease):
    """Verify this expired lease's frozen delivery before locking its job; caller commits."""
    result, result_hash = (lease.limits or {}).get('delivery_result'), lease.result_hash
    if (lease.completed_at or lease.expires_at > now() or not result or digest(result) != result_hash
            or not accepted_delivery(lease)):
        return False
    try:
        if result['output'] is not None:
            with get_store().path(lease.output_key).open('rb') as handle:
                verify_output(handle, result)
    except (OSError, ValueError, HTTPException):
        return False
    lock_lease_jobs(db, [lease.id])
    db.refresh(lease)
    if lease.result_hash != result_hash or lease.expires_at > now():
        return False
    try:
        stage, job = delivery_target(db, lease)
    except ProcessingError:
        return False
    commit_result(db, lease, stage, job, result, lease.limits.get('delivery_timings', {}))
    return True


class CompletionRequest(LeaseRequest):
    error: NodeError
    timings: Timings = Field(default_factory=dict)


@router.post('/leases/{lease_id}/complete')
def complete(lease_id: str, body: CompletionRequest, identity=Depends(node_auth)):
    result_hash = digest({'error': body.error.model_dump()})
    with session_factory()() as db:
        lease = scoped_lease(db, lease_id, body.lease_token, identity, lock=True)
        if lease.completed_at:
            if body.error.code == 'LEASE_STOPPED' or lease.result_hash == result_hash:
                return receipt(lease)
            problem('COMPLETION_CONFLICT', '租约已结束', 409)
        if body.error.code == 'LEASE_STOPPED':
            stage, job = db.get(JobStage, lease.stage_id), db.get(Job, lease.job_id)
            if stage.generation != lease.generation:
                release_lease(db, lease, 'cancelled')
                db.commit()
                return receipt(lease)
            invalid = lease.expires_at <= now() or job.status != 'running' or job.cancel_requested or job.discard_output
            invalid = invalid or not available(db.get(Asset, job.input_asset_id)) or lease_deadline(db, lease) <= now()
            if not invalid:
                problem('LEASE_ACTIVE', '执行授权仍有效', 409)
            if (accepted_delivery(lease) and parsed(lease.limits['delivery_persist_deadline_at']) > now()
                    and job.status == 'running' and not job.cancel_requested and not job.discard_output):
                # A node's stop acknowledgement cannot cancel a body already
                # accepted by the center while its bounded disk publish runs.
                raise ProcessingError('STORAGE_UNAVAILABLE', '已受理结果正在持久化，请重试完成确认')
            lease.expires_at = min(lease.expires_at, now())
        else:
            live_page(db, lease_id)
            lease.result_hash = result_hash
        db.commit()
    if body.error.code == 'LEASE_STOPPED':
        from .dispatcher import recover_lease
        recover_lease(lease_id)
    else:
        fail_stage(lease_id, node_error(body.error.code),
                   token=body.lease_token, node_id=identity)
    with session_factory()() as db:
        lease = scoped_lease(db, lease_id, body.lease_token, identity)
        if not lease.completed_at:
            raise ProcessingError('STORAGE_UNAVAILABLE', '完成状态尚未确认，请重试同一请求')
        return receipt(lease)
