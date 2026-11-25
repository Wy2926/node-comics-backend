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

    monkeypatch.setattr(compute_v3, 'lock_lease_jobs', unexpected)
    monkeypatch.setattr(compute_v3, 'live_page', unexpected)
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


def test_unchanged_page_batches_do_not_fetch_bodies_or_repeat_queries(v3):
    from app import compute_channel
    from compute_pull import body as pull_body, connect
    from test_compute_claims import capacity
    from test_compute_v3 import analyze, text
    from test_document_borrowing import statements

    capacity(v3, 8)
    jobs = v3['create'](8)
    leases = claim(v3, 4, 'one') + claim(v3, 4, 'two')
    assert {p['job_id'] for p in leases} == set(jobs)
    for p in leases:
        analyze(v3, p)
        text(p)
    node = v3['node']['node_id']
    connection = compute_channel.authenticated(node, v3['auth']['Authorization'])
    with statements() as sql:
        updates = compute_channel.snapshot(node, connection, {}, None)[1]
    assert sum('classic_states.translations' in s for s in sql) == 1
    seen = {u['lease_id']: u['translations']['revision'] for u in updates}
    assert len(seen) == 8
    stale = leases[-1]['lease_id']
    partial = {**seen, stale: 'outdated'}
    changed, = compute_channel.snapshot(node, connection, partial, None)[1]
    assert (changed['lease_id'], changed['translations']['revision']) == (stale, seen[stale])

    for batch in (leases[:1], leases):
        body = compute_v3.HeartbeatRequest(config_version=1, leases=[{
            'lease_id': p['lease_id'], 'lease_token': p['lease_token'], 'translations_revision': seen[p['lease_id']]}
            for p in batch])
        with session_factory()() as db, statements() as sql:
            result = compute_v3.heartbeat(node, body, node, db)
        assert [r['lease_id'] for r in result['leases']] == [p['lease_id'] for p in batch]
        assert all(r['status'] == 'active' and 'translations' not in r for r in result['leases'])
        assert not any('pg_advisory_xact_lock' in s for s in sql)
        assert len(sql) <= 6 and not any('classic_states.translations' in s for s in sql), sql
    with statements() as sql:
        result = compute_channel.snapshot(node, connection, seen, None)
    assert not result[1] and result[2] == seen.keys()
    assert len(sql) <= 2 and not any('classic_states.translations' in s for s in sql), sql

    with connect(v3) as socket:
        assert socket.receive_json()['config']['version'] == 1
        assert {socket.receive_json()['leases'][0]['lease_id'] for _ in leases} == seen.keys()
        # Each fresh pull crosses another snapshot on this connection without a lease update frame.
        with statements() as sql:
            for index in range(4):
                request = pull_body(request_id=f'full-{index}')
                socket.send_json(request)
                reply = socket.receive_json()
                assert (reply['type'], reply['request_id'], reply['leases']) == ('assignment', request['request_id'], [])
        assert not any('classic_states.translations' in s for s in sql)
