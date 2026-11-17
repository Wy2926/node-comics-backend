"""Fast claim paths skip election without weakening durable receipts or fencing."""
import pytest

from app import compute_v3, scheduler
from app.db import session_factory
from app.models import Job
from app.queue_models import ComputeClaim
from test_compute_v3 import claim, v3


def no_election(*args, **kwargs):
    raise AssertionError('This path must not rank the task queue')


def test_input_download_only_reads_identity_and_file_metadata(v3, monkeypatch):
    from datetime import timedelta
    from sqlalchemy import event
    from app.db import engine
    from app.models import now
    from app.queue_models import ExecutionLease, JobStage
    from app.storage import LocalStore
    from test_compute_v3 import read_input

    v3['create']()
    lease = claim(v3)[0]
    with session_factory()() as db:
        saved = db.get(ExecutionLease, lease['lease_id'])
        saved.expires_at = now() - timedelta(seconds=1)
        saved.limits = {**saved.limits, 'deadline_at': saved.expires_at.isoformat() + 'Z'}
        db.get(JobStage, saved.stage_id).generation += 1
        db.get(Job, saved.job_id).cancel_requested = True
        db.commit()

    def unexpected(*args, **kwargs):
        pytest.fail('File reads must not lock, validate execution state or stat before opening')

    monkeypatch.setattr(compute_v3, 'lock_scheduler', unexpected)
    monkeypatch.setattr(compute_v3, 'live_lease', unexpected)
    monkeypatch.setattr(LocalStore, 'exists', unexpected)
    statements = []
    def track(connection, cursor, statement, parameters, context, executemany):
        statements.append(statement)

    event.listen(engine(), 'before_cursor_execute', track)
    try:
        response = read_input(v3, lease)
    finally:
        event.remove(engine(), 'before_cursor_execute', track)
    assert response.status_code == 200
    assert len(statements) == 2
    assert all(statement.lstrip().upper().startswith('SELECT ') for statement in statements)
    assert 'classic_states' not in ' '.join(statements)


def test_full_node_and_replay_do_not_rank_the_queue(v3, monkeypatch):
    v3['create'](8)
    first = claim(v3, 4, 'saved')
    assert len(first) == 4
    lease_ids = [lease['lease_id'] for lease in first]
    monkeypatch.setattr(scheduler, '_candidate_rows', no_election)
    assert [lease['lease_id'] for lease in claim(v3, 4, 'saved')] == lease_ids
    assert claim(v3, 4, 'full') == []
    with session_factory()() as db:
        assert db.get(ComputeClaim, (v3['node']['node_id'], 'full')) is None
