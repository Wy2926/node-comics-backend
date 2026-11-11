"""Whole-page calls, durable retries and released text capacity without upstream traffic."""
from datetime import timedelta
import json

import pytest
from sqlalchemy import func, select

from app import classic, workers
from app.adapters.llm import TextError, TextResponse
from app.db import session_factory
from app.jobs import cancel_job
from app.models import ClassicState, Job, TextCall, now, uid
from app.queue_models import ComputeNode, ExecutionLease, JobStage
from app.scheduler import claim_stage, remaining_capacity
from test_classic import analysis, text_case, text_database  # noqa: F401


def test_full_200_region_page_uses_one_call_and_replays_saved_text(text_case, monkeypatch):
    segments = [{'id': str(index), 'source': '字' * (4000 if index == 0 else 40)} for index in range(200)]
    expected = {segment['id']: 'translated ' + segment['id'] for segment in segments}
    with session_factory()() as db:
        db.get(ClassicState, text_case[0]).analysis = analysis(segments)
        db.commit()
    calls = []
    def translate(rows, *args):
        calls.append(rows)
        return TextResponse(json.dumps(expected), {'input_tokens': 5000, 'output_tokens': 1000}, 'whole-page')
    monkeypatch.setattr(classic, 'call_text', translate)
    for _ in range(2):
        assert classic.run_text_stage(*text_case) == {'translations': expected}
    assert calls == [segments]
    with session_factory()() as db:
        assert db.scalar(select(func.count()).select_from(TextCall)) == 1


def test_partial_old_checkpoint_is_replaced_by_complete_page_with_fresh_call_budget(text_case, monkeypatch):
    job_id, lease_id = text_case
    with session_factory()() as db:
        job = db.get(Job, job_id)
        state = db.get(ClassicState, job_id)
        state.analysis = analysis([{'id': str(index), 'source': 'source'} for index in range(5)])
        state.translations = {'0': '', '1': 'paid', '2': 'paid'}
        for index in range(3):
            db.add(TextCall(job_id=job_id, attempt_id=job.attempt_id, execution_lease_id=lease_id,
                group_index=index, sequence=1, provider_id=job.config['text']['provider_id'], model='old',
                reserved_micros=10, accounted_micros=10, cost_state='estimated', completed_at=now()))
        db.commit()
    calls = []
    def translate(rows, *args):
        calls.append(rows)
        return TextResponse(json.dumps({str(index): 'new' for index in range(5)}), None, 'resumed')
    monkeypatch.setattr(classic, 'call_text', translate)
    assert classic.run_text_stage(*text_case)['translations'] == {str(index): 'new' for index in range(5)}
    assert [[row['id'] for row in rows] for rows in calls] == [['0', '1', '2', '3', '4']]
    with session_factory()() as db:
        assert db.scalar(select(func.count()).select_from(TextCall)) == 4
        call = db.scalar(select(TextCall).where(TextCall.group_index == classic.PAGE_CALL_INDEX))
        assert call.sequence == 1


@pytest.mark.parametrize('outcome', ['success', 'exhausted', 'cancelled'])
def test_retry_after_releases_slot_and_restores_bounded_attempts(text_case, monkeypatch, outcome):
    job_id, lease_id = text_case
    with session_factory()() as db:
        node = db.get(ComputeNode, 'text-node')
        node.capacity, node.engine_version = 1, 'control'
        db.commit()
    calls = []
    def translate(*args):
        calls.append(1)
        if outcome == 'success' and len(calls) == 2:
            return TextResponse('{"b001":"translated"}', None, 'success')
        raise TextError('TEXT_PROVIDER_REJECTED', 'Known HTTP 503', retryable=True, retry_after=90)
    monkeypatch.setattr(classic, 'call_text', translate)
    workers.run_control_stage(lease_id)
    with session_factory()() as db:
        lease = db.get(ExecutionLease, lease_id)
        stage = db.get(JobStage, lease.stage_id)
        job = db.get(Job, job_id)
        assert lease.completed_at and stage.status == 'ready'
        assert stage.available_at > now() + timedelta(seconds=89)
        assert remaining_capacity(db, db.get(ComputeNode, 'text-node')) == 1
        assert claim_stage(db, 'text-node') is None
        other = Job(owner_id=job.owner_id, input_asset_id=job.input_asset_id, config=job.config,
            mode=job.mode, target_language=job.target_language, status='queued', quota_pages=0,
            quota_kind='unlimited', settlement='free', operation='translation',
            idempotency_key=uid(), request_hash='b' * 64, cache_key=uid())
        db.add(other)
        db.flush()
        db.add(JobStage(job_id=other.id, name='text', status='ready'))
        db.commit()
        admitted = claim_stage(db, 'text-node')
        assert admitted and admitted.job_id == other.id
        db.commit()
    workers.complete_stage(admitted.id, {})
    if outcome == 'cancelled':
        with session_factory()() as db:
            cancel_job(db, db.get(Job, job_id))
            db.commit()
            assert claim_stage(db, 'text-node') is None
            assert db.get(Job, job_id).status == 'cancelled'
        assert len(calls) == 1
        return
    for _ in range(1 if outcome == 'success' else 2):
        with session_factory()() as db:
            db.get(JobStage, stage.id).available_at = now()
            db.commit()
            resumed = claim_stage(db, 'text-node')
            assert resumed.job_id == job_id
            db.commit()
        workers.run_control_stage(resumed.id)
    with session_factory()() as db:
        records = db.scalars(select(TextCall).where(TextCall.job_id == job_id).order_by(TextCall.sequence)).all()
        assert [call.sequence for call in records] == list(range(1, len(calls) + 1))
        assert len(records) == (2 if outcome == 'success' else 3)
        assert db.get(JobStage, stage.id).status == ('succeeded' if outcome == 'success' else 'failed')
        assert remaining_capacity(db, db.get(ComputeNode, 'text-node')) == 1


def test_partial_reply_never_merges_with_old_regions(text_case, monkeypatch):
    with session_factory()() as db:
        state = db.get(ClassicState, text_case[0])
        state.analysis = analysis([{'id': 'first', 'source': 'context'}, {'id': 'second', 'source': 'response'}])
        state.translations = {'first': 'old'}
        db.commit()
    calls = []
    def translate(segments, *args):
        calls.append([segment['id'] for segment in segments])
        return TextResponse('{"second":"partial"}', None, 'partial')
    monkeypatch.setattr(classic, 'call_text', translate)
    with pytest.raises(TextError, match='TEXT_INVALID_RESPONSE'):
        classic.run_text_stage(*text_case)
    assert calls == [['first', 'second']]
    with session_factory()() as db:
        assert db.get(ClassicState, text_case[0]).translations == {'first': 'old'}


@pytest.mark.parametrize('interrupted', ['transport', 'worker'])
def test_unknown_call_releases_capacity_without_resend_or_settlement(text_case, monkeypatch, interrupted):
    job_id, lease_id = text_case
    with session_factory()() as db:
        job = db.get(Job, job_id)
        page = JobStage(job_id=job_id, name='page', status='running', generation=1)
        db.add(page)
        db.flush()
        page_lease = ExecutionLease(job_id=job_id, stage_id=page.id, node_id='text-node', owner_id=job.owner_id,
            generation=1, resource_pool='image', mode='classic', expires_at=now() + timedelta(minutes=5))
        db.add(page_lease)
        db.commit()
    if interrupted == 'transport':
        def timeout(*args):
            raise TextError('TEXT_TRANSPORT_FAILED', 'read timed out', unknown=True)
        monkeypatch.setattr(classic, 'call_text', timeout)
        workers.run_control_stage(lease_id)
    else:
        classic.reserve_call(job_id, lease_id, [{'id': 'b001', 'source': 'Hello!'}], 'zh-Hans')
        with session_factory()() as db:
            db.get(ExecutionLease, lease_id).expires_at = now() - timedelta(seconds=1)
            db.commit()
        workers.fail_stage(lease_id, classic.ProcessingError('WORKER_LEASE_EXPIRED', 'lost'), recovering=True)
    with session_factory()() as db:
        lease = db.get(ExecutionLease, lease_id)
        assert lease.completed_at
        assert db.get(JobStage, lease.stage_id).status == 'waiting'
        job = db.get(Job, job_id)
        assert job.status == 'outcome_unknown' and job.settlement == 'reserved'
        assert claim_stage(db, 'text-node') is None
        calls = db.scalars(select(TextCall)).all()
        assert len(calls) == 1 and calls[0].cost_state == 'unknown' and calls[0].accounted_micros > 0
    # The image worker stopping after the text pause must not settle the unknown call.
    workers.fail_stage(page_lease.id, classic.ProcessingError('WORKER_LEASE_EXPIRED', 'stopped'))
    with session_factory()() as db:
        job = db.get(Job, job_id)
        assert job.status == 'outcome_unknown' and job.settlement == 'reserved'
        assert db.get(ExecutionLease, page_lease.id).completed_at
        assert remaining_capacity(db, db.get(ComputeNode, 'text-node')) == 4
