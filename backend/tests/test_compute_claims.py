"""Claim receipt concurrency across the lock-free preparation boundary."""
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier

import pytest
from fastapi import HTTPException
from sqlalchemy import func, select

from app import compute_channel, scheduler
from app.db import session_factory
from app.models import Job
from app.queue_models import ComputeClaim, ComputeNode, ExecutionLease
from compute_pull import bound_pull
from test_compute_v3 import v3  # noqa: F401


def capacity(v3, size):
    with session_factory()() as db:
        node = db.get(ComputeNode, v3['node']['node_id'])
        node.capacity = size
        node.desired_config = {**node.desired_config, 'execution_slots': size}
        db.commit()


def synchronized_preparation(monkeypatch, participants):
    barrier = Barrier(participants)
    original = compute_channel.prepare_claim_candidates
    def prepare(*args, **kwargs):
        snapshot = original(*args, **kwargs)
        barrier.wait(timeout=10)
        return snapshot
    monkeypatch.setattr(compute_channel, 'prepare_claim_candidates', prepare)


def test_same_claim_receipt_concurrent_retries_allocate_one_batch(v3, monkeypatch):
    v3['create'](12)
    capacity(v3, 12)
    synchronized_preparation(monkeypatch, 3)
    pull = bound_pull(v3)
    with ThreadPoolExecutor(max_workers=3) as pool:
        replies = list(pool.map(lambda _: pull(4, 'shared-receipt'), range(3)))
    batches = [[lease['lease_id'] for lease in batch] for batch in replies]
    assert len(batches[0]) == 4 and batches[0] == batches[1] == batches[2]
    with session_factory()() as db:
        assert db.scalar(select(func.count()).select_from(ExecutionLease)) == 4
        receipts = db.scalars(select(ComputeClaim)).all()
        assert len(receipts) == 1 and receipts[0].lease_ids == batches[0]


def test_same_claim_id_with_concurrent_changed_count_conflicts(v3, monkeypatch):
    v3['create'](8)
    capacity(v3, 8)
    synchronized_preparation(monkeypatch, 2)
    pull = bound_pull(v3)
    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(pull, count, 'conflicting-receipt') for count in (3, 4)]
    errors = [f.exception() for f in futures if f.exception()]
    assert len(errors) == 1 and errors[0].detail['code'] == 'CLAIM_CONFLICT'
    winner, = [f.result() for f in futures if not f.exception()]
    with session_factory()() as db:
        assert db.scalar(select(func.count()).select_from(ExecutionLease)) == len(winner)
        assert db.scalar(select(func.count()).select_from(ComputeClaim)) == 1


@pytest.mark.parametrize('mutation', ['configuration', 'cancel_and_shrink'])
def test_prepared_claim_rechecks_intervening_changes(v3, monkeypatch, mutation):
    jobs = v3['create'](8)
    capacity(v3, 8)
    pull = bound_pull(v3)
    original = compute_channel.prepare_claim_candidates
    def prepare(*args, **kwargs):
        snapshot = original(*args, **kwargs)
        with session_factory()() as writer:
            scheduler.lock_scheduler(writer)
            node = writer.get(ComputeNode, v3['node']['node_id'])
            node.capacity = 1
            node.desired_config = {**node.desired_config, 'execution_slots': 1}
            if mutation == 'configuration':
                node.config_version += 1
            else:
                job = writer.get(Job, jobs[0])
                job.cancel_requested = True
                scheduler.touch_job(writer, job)
            writer.commit()
        return snapshot
    monkeypatch.setattr(compute_channel, 'prepare_claim_candidates', prepare)
    if mutation == 'configuration':
        with pytest.raises(HTTPException) as rejected:
            pull(4, 'prepared-change')
        assert rejected.value.detail['code'] == 'NODE_CONFIG_CONFLICT'
        expected = 0
    else:
        leases = pull(4, 'prepared-change')
        assert len(leases) == 1 and leases[0]['job_id'] != jobs[0]
        expected = 1
    with session_factory()() as db:
        assert db.scalar(select(func.count()).select_from(ExecutionLease)) == expected
        assert db.scalar(select(func.count()).select_from(ComputeClaim)) == expected
