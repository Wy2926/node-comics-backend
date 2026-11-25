"""Real PostgreSQL writers: unrelated work proceeds; a waiter rereads its own job."""
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from datetime import timedelta
from threading import Event, current_thread

import pytest
from sqlalchemy import event, select

from app import compute_v3, dispatcher, scheduler
from app.db import engine, session_factory
from app.models import Asset, Job, Ledger, now
from app.queue_models import ExecutionLease, JobStage
from app.storage import LocalStore
from test_compute_multinode import peer
from test_compute_v3 import analyze, claim, deliver, heartbeat, result_for, text, v3


@contextmanager
def waiting_for_job(work):
    reached = Event()
    def observe(connection, cursor, sql, *_):
        if current_thread().name.startswith('job-waiter') and 'FROM jobs' in sql and 'FOR NO KEY UPDATE' in sql:
            reached.set()
    bind = engine()
    event.listen(bind, 'before_cursor_execute', observe)
    try:
        with ThreadPoolExecutor(1, thread_name_prefix='job-waiter') as pool:
            future = pool.submit(work)
            assert reached.wait(5), 'The writer must take the job row before using its previous observation'
            yield
            future.result(timeout=5)
    finally:
        event.remove(bind, 'before_cursor_execute', observe)


def test_old_global_lock_and_busy_peer_do_not_block_another_page_lifecycle(v3, png):
    from app.control_pools import report_pools
    from conftest import login, request_for_job
    other = peer(v3)
    v3['create'](2)
    busy = claim(other, 1)[0]
    user = login(v3['client'], 'reader0')
    with session_factory()() as blocker:
        scheduler.lock_scheduler(blocker)
        scheduler.locked_node(blocker, other['node']['node_id'])
        scheduler.lock_jobs(blocker, [busy['job_id']])
        with session_factory()() as db:
            report_pools(db)
        assert heartbeat(v3, []).json()['leases'] == []
        page, = claim(v3, 1)
        assert page['job_id'] != busy['job_id']
        assert heartbeat(v3, [page]).json()['leases'][0]['status'] == 'active'
        analyze(v3, page)
        text(page)
        result, data = result_for(v3, page, png)
        first = deliver(v3, page, result, data)
        assert first.status_code == 200 and first.json()['job_status'] == 'succeeded'
        assert deliver(v3, page, result, data).json() == first.json()
        assert heartbeat(v3, [page]).json()['leases'] == [first.json()]
        with session_factory()() as held_job:
            scheduler.lock_jobs(held_job, [page['job_id']])
            assert deliver(v3, page, result, data).json() == first.json()
        alias = request_for_job(v3['client'], user, page['job_id'])
        assert v3['client'].delete('/v1/translations/' + alias, headers=user).json()['deleted']
    with session_factory()() as db:
        assert db.scalars(select(Ledger.kind).where(Ledger.job_id == page['job_id']).order_by(Ledger.kind)).all() == ['reserve', 'settle']


@pytest.mark.parametrize('change', ['cancel', 'generation'])
def test_waiting_heartbeat_does_not_extend_a_stopped_job(v3, change):
    job_id, = v3['create']()
    page, = claim(v3)
    node = v3['node']['node_id']
    request = compute_v3.HeartbeatRequest(config_version=1, leases=[{
        'lease_id': page['lease_id'], 'lease_token': page['lease_token']}])
    def renew():
        with session_factory()() as db:
            reply, = compute_v3.heartbeat(node, request, node, db)['leases']
            assert (reply['status'], reply['code']) == ('stop', 'LEASE_EXPIRED')
    with session_factory()() as db:
        scheduler.lock_jobs(db, [job_id])
        lease = db.get(ExecutionLease, page['lease_id'])
        expires = lease.expires_at
        if change == 'cancel':
            db.get(Job, job_id).cancel_requested = True
        else:
            db.get(JobStage, lease.stage_id).generation += 1
        db.flush()
        with waiting_for_job(renew):
            db.commit()
    with session_factory()() as db:
        assert db.get(ExecutionLease, page['lease_id']).expires_at == expires


@pytest.mark.parametrize('missing_file', [False, True])
def test_recovery_rechecks_an_inflight_renewal_even_after_observing_a_missing_file(v3, missing_file):
    job_id, = v3['create']()
    page, = claim(v3)
    key = page['lease_id']
    with session_factory()() as db:
        db.get(ExecutionLease, key).expires_at = now() - timedelta(seconds=1)
        if missing_file:
            LocalStore().delete(db.get(Asset, db.get(Job, job_id).input_asset_id).storage_key)
        db.commit()
        scheduler.lock_jobs(db, [job_id])
        new_deadline = now() + timedelta(minutes=1)
        db.get(ExecutionLease, key).expires_at = new_deadline
        db.flush()
        with waiting_for_job(lambda: dispatcher.recover_lease(key)):
            db.commit()
    with session_factory()() as db:
        lease = db.get(ExecutionLease, key)
        assert lease.expires_at == new_deadline and not lease.completed_at
        assert db.get(JobStage, lease.stage_id).status == db.get(Job, job_id).status == 'running'


def test_recovery_of_one_frozen_delivery_cannot_publish_another(v3, png, monkeypatch):
    v3['create'](2)
    pages = claim(v3, 2)
    with monkeypatch.context() as m:
        def crash(*_):
            raise RuntimeError('before final SQL')
        m.setattr(compute_v3, 'commit_result', crash)
        for page in pages:
            analyze(v3, page)
            text(page)
            result, _ = result_for(v3, page, png)
            result.update(representation='original', bbox=None, output=None)
            with pytest.raises(RuntimeError, match='before final SQL'):
                deliver(v3, page, result)
    with session_factory()() as db:
        for page in pages:
            db.get(ExecutionLease, page['lease_id']).expires_at = now() - timedelta(seconds=1)
        db.commit()
    dispatcher.recover_lease(pages[0]['lease_id'])
    with session_factory()() as db:
        assert [db.get(Job, p['job_id']).status for p in pages] == ['succeeded', 'running']
    dispatcher.recover_once()
    with session_factory()() as db:
        assert db.get(Job, pages[1]['job_id']).status == 'succeeded'
