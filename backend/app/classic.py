"""Control-plane text stages and fenced OCR checkpoints."""
import base64
from decimal import Decimal
from io import BytesIO
import json
import math
from sqlalchemy import func, or_, select
from .adapters.llm import TextError
from .adapters.text import LANGUAGE_CODE_VERSIONS, LEGACY_SYSTEM, call_text, input_bound, output_token_limit, parse_translations
from .assets import available
from .db import session_factory
from .errors import ProcessingError, node_error
from .image_metadata import image_metadata
from .models import Asset, ClassicState, Job, TextCall, now, uid
from .translation_provider_limits import reserve_request, release_unstarted_request

MAX_CHECKPOINT_BYTES = 5 * 1024 * 1024
MAX_IMAGE_BYTES = 24 * 1024 * 1024
# Historical grouped calls retain their nonnegative indexes and accounting.
PAGE_CALL_INDEX = -1
UNKNOWN_TEXT_ERRORS = {'TEXT_TRANSPORT_FAILED', 'TEXT_CALL_INTERRUPTED', 'TEXT_OUTCOME_UNKNOWN'}


def decode_bounded(value, limit=MAX_IMAGE_BYTES):
    if not isinstance(value, str) or len(value) > ((limit + 2) // 3) * 4:
        raise ValueError('Invalid encoded image size')
    raw = base64.b64decode(value, validate=True)
    if len(raw) > limit:
        raise ValueError('Invalid image size')
    return raw


def current(db, job_id, lease_id):
    from .scheduler import current_lease, lock_scheduler
    lock_scheduler(db)
    lease, stage, job = current_lease(db, lease_id)
    if job.id != job_id or stage.name != 'text':
        raise ProcessingError('LEASE_EXPIRED', '文本执行租约已失效')
    if job.cancel_requested or job.discard_output or not available(db.get(Asset, job.input_asset_id)):
        raise ProcessingError('ASSET_EXPIRED', '原图已删除、过期或任务已取消')
    return job


def text_remaining(db, job):
    started = db.scalar(select(func.min(TextCall.started_at)).where(TextCall.job_id == job.id,
        or_(TextCall.error_code.is_(None), TextCall.error_code != 'TEXT_PROVIDER_DISABLED')))
    elapsed = (now() - started).total_seconds() if started else 0
    return job.config['provider']['timeout_seconds'] - elapsed


def validate_analysis(result, width, height):
    try:
        if len(json.dumps(result, allow_nan=False).encode()) > MAX_CHECKPOINT_BYTES:
            raise node_error('CLASSIC_ANALYSIS_TOO_LARGE')
        segments = result['segments']
        if result['width'] != width or result['height'] != height or not isinstance(segments, list):
            raise ValueError()
        if len(segments) > 200:
            raise node_error('CLASSIC_REGION_LIMIT_EXCEEDED')
        if len(result['regions']) != len(segments):
            raise ValueError()
        seen = set()
        for segment in segments:
            if set(segment) != {'id', 'source'} or not isinstance(segment['id'], str) or not 1 <= len(segment['id']) <= 80 or segment['id'] in seen:
                raise ValueError()
            if not isinstance(segment['source'], str) or not segment['source'].strip():
                raise ValueError()
            if len(segment['source']) > 4000:
                raise node_error('CLASSIC_TEXT_LIMIT_EXCEEDED')
            seen.add(segment['id'])
        if segments:
            mask_bytes = decode_bounded(result['mask'], MAX_CHECKPOINT_BYTES)
            mask = image_metadata(BytesIO(mask_bytes))
            if mask['mime'] != 'image/png' or (mask['width'], mask['height']) != (width, height):
                raise ValueError()
            for region in result['regions']:
                lines = region['lines']
                if not isinstance(lines, list) or not 1 <= len(lines) <= 500:
                    raise ValueError()
                for polygon in lines:
                    if not isinstance(polygon, list) or len(polygon) != 4:
                        raise ValueError()
                    for point in polygon:
                        if len(point) != 2 or any(isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v) for v in point):
                            raise ValueError()
                        if not (-width <= point[0] <= 2 * width and -height <= point[1] <= 2 * height):
                            raise ValueError()
        elif result.get('mask') is not None:
            raise ValueError()
    except (KeyError, ValueError, TypeError, OSError):
        raise ProcessingError('CLASSIC_OCR_INVALID', 'OCR 区域或掩膜无效，未调用文本服务') from None


def _cost_micros(profile, input_tokens, output_tokens):
    # CNY / million tokens equals micro-CNY / token. Round the combined cost
    # up once, using decimal arithmetic to avoid binary-float boundary errors.
    return math.ceil(input_tokens * Decimal(str(profile['input_rate'])) +
                     output_tokens * Decimal(str(profile['output_rate'])))


def unresolved_call(db, job_id):
    return db.scalar(select(TextCall).where(TextCall.job_id == job_id,
        or_(TextCall.completed_at.is_(None), TextCall.error_code.in_(UNKNOWN_TEXT_ERRORS)))
        .order_by(TextCall.started_at).limit(1))


def reserve_call(job_id, lease_id, segments, language):
    with session_factory()() as db:
        job = current(db, job_id, lease_id)
        profile = job.config['text']
        from .translation_providers import require_enabled
        provider = require_enabled(db, profile)
        if text_remaining(db, job) < profile['timeout_seconds']:
            raise TextError('TEXT_DEADLINE_EXCEEDED', '此页已达到文本处理总时限')
        pending = unresolved_call(db, job_id)
        if pending:
            if pending.execution_lease_id == lease_id and not pending.completed_at:
                raise TextError('TEXT_CALL_IN_FLIGHT', '当前页面已经在执行文本调用')
            raise TextError('TEXT_OUTCOME_UNKNOWN', '已有文本调用结果待核实，不会重复发送', unknown=True)
        count, sequence = db.execute(select(
            func.count().filter(or_(TextCall.error_code.is_(None), TextCall.error_code != 'TEXT_PROVIDER_DISABLED')),
            func.coalesce(func.max(TextCall.sequence), 0)).where(TextCall.job_id == job_id, TextCall.group_index == PAGE_CALL_INDEX)).one()
        if count >= profile['max_attempts']:
            raise TextError('TEXT_RETRY_EXHAUSTED', '此页已达到自动调用次数上限')
        prompt = job.config.get('text_prompt', LEGACY_SYSTEM)
        language_names = job.config.get('prompt_version', 'comic-json-v9') not in LANGUAGE_CODE_VERSIONS
        reserved = _cost_micros(profile, input_bound(segments, language, prompt, language_names),
                               output_token_limit(segments, profile['max_output_tokens']))
        call = TextCall(id=uid(), job_id=job_id, attempt_id=job.attempt_id, execution_lease_id=lease_id,
                        group_index=PAGE_CALL_INDEX, sequence=sequence + 1,
                        provider_id=profile['provider_id'], model=profile['model'], reserved_micros=reserved, accounted_micros=reserved)
        reserve_request(provider, call.id)
        db.add(call)
        db.commit()  # Intent and full unknown-cost reservation must exist before any request.
        return call.id, profile, count + 1, prompt, language_names


def complete_call(call_id, lease_id, response=None, error=None, translations=None):
    with session_factory()() as db:
        from .scheduler import lock_scheduler
        lock_scheduler(db)
        call = db.scalar(select(TextCall).where(TextCall.id == call_id).with_for_update())
        if call.completed_at:
            return
        usage = response.usage if response else getattr(error, 'usage', None)
        call.request_id = response.request_id if response else getattr(error, 'request_id', None)
        call.error_code = 'TEXT_OUTCOME_UNKNOWN' if getattr(error, 'unknown', False) else getattr(error, 'code', None)
        release_unstarted_request(call.provider_id, call_id, error)
        call.completed_at = now()
        job = db.scalar(select(Job).where(Job.id == call.job_id).with_for_update(key_share=True))
        if usage is not None:
            call.usage, call.cost_state = usage, 'estimated'
            profile = job.config['text']
            call.accounted_micros = _cost_micros(profile, usage['input_tokens'], usage['output_tokens'])
        # Cost belongs to the original call even after cancellation or lease loss.
        # Translation checkpoints belong only to the current execution generation.
        if translations:
            try:
                current(db, job.id, lease_id)
            except ProcessingError:
                pass
            else:
                state = db.get(ClassicState, job.id)
                if state is not None:
                    state.translations = translations
        db.commit()


def translate_page(job_id, lease_id, segments, language):
    try:
        call_id, profile, sequence, prompt, language_names = reserve_call(job_id, lease_id, segments, language)
    except TextError as error:
        if error.code == 'TEXT_RATE_LIMITED':
            with session_factory()() as db:
                job = current(db, job_id, lease_id)
                if error.retry_after + job.config['text']['timeout_seconds'] > text_remaining(db, job):
                    raise TextError('TEXT_DEADLINE_EXCEEDED', '供应商限流等待超过此页剩余时限') from None
        raise
    response = None
    try:
        response = call_text(segments, language, profile, prompt, language_names)
        translations = parse_translations(response.content, segments)
        complete_call(call_id, lease_id, response=response, translations=translations)
    except TextError as error:
        complete_call(call_id, lease_id, response=response, error=error)
        if error.unknown or error.code in UNKNOWN_TEXT_ERRORS:
            error.retryable = False
            error.unknown = True
            raise
        if sequence >= profile['max_attempts']:
            if error.code == 'TEXT_RATE_LIMITED':
                raise TextError('TEXT_RETRY_EXHAUSTED', '此页已达到自动调用次数上限') from None
            error.retryable = False
        if error.retryable:
            delay = max(error.retry_after, min(2 ** (sequence - 1), 8))
            with session_factory()() as db:
                job = current(db, job_id, lease_id)
                remaining = text_remaining(db, job)
            if delay + profile['timeout_seconds'] > remaining:
                raise TextError('TEXT_DEADLINE_EXCEEDED', '供应商要求等待的时间超过此页剩余时限')
            error.retry_after = delay
        # The scheduler persists the retry deadline and releases this slot.
        raise
    except Exception:
        complete_call(call_id, lease_id, error=TextError('TEXT_CALL_INTERRUPTED', '文本调用中断，保守保留成本预占'))
        raise


def run_text_stage(job_id, lease_id):
    """Run only LLM work. Image stages run on independently leased devices."""
    with session_factory()() as db:
        job = current(db, job_id, lease_id)
        state = db.get(ClassicState, job_id)
        if not state or not state.analysis:
            raise ProcessingError('CLASSIC_OCR_MISSING', '缺少已验证 OCR 检查点')
        # Partial checkpoints cannot supply full-page context. Replace them only
        # after one complete response; never translate or merge a subset of IDs.
        segments = state.analysis['segments']
        complete = set(state.translations) == {segment['id'] for segment in segments}
        language = job.target_language
    if segments and not complete:
        translate_page(job_id, lease_id, segments, language)
    with session_factory()() as db:
        current(db, job_id, lease_id)
        return {'translations': db.get(ClassicState, job_id).translations}
