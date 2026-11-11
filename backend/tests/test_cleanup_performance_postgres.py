"""Pending cleanup remains indexed as purged history grows; run only in the dedicated test database."""
from datetime import timedelta
import os
from uuid import uuid4

import pytest
from sqlalchemy import event, select, text

from test_postgres_concurrency import pg_scope, pg, new_job

pytestmark = pytest.mark.skipif(os.environ.get('RUN_POSTGRES_CONCURRENCY') != '1',
    reason='Requires dedicated PostgreSQL test database')


def test_cleanup_uses_pending_indexes_and_preserves_referenced_files(pg):
    from app.db import engine, session_factory
    from app.dispatcher import cleanup
    from app.models import Asset, Job, now

    job_id = new_job(pg)
    at = now()
    with session_factory()() as db:
        source = dict(db.execute(select(Asset.__table__).where(Asset.id == pg['asset_id'])).mappings().one())
        job = dict(db.execute(select(Job.__table__).where(Job.id == job_id)).mappings().one())
        history, jobs = [], []
        for i in range(5000):
            original, output, identity = str(uuid4()), str(uuid4()), str(uuid4())
            history.extend([
                {**source, 'id': original, 'purged_at': at, 'storage_key': f'inputs/{original}/source'},
                {**source, 'id': output, 'kind': 'classic', 'deleted_at': at if i >= 20 else None,
                 'purged_at': at if i >= 20 else None, 'created_at': at - timedelta(days=2),
                 'storage_key': f'results/{output}'},
            ])
            jobs.append({**job, 'id': identity, 'idempotency_key': identity, 'status': 'succeeded',
                'discard_output': True, 'completed_at': at, 'input_asset_id': original, 'output_asset_id': output})
        orphan = str(uuid4())
        history.append({**source, 'id': orphan, 'kind': 'classic', 'created_at': at - timedelta(days=2),
            'storage_key': f'results/{orphan}'})
        db.execute(Asset.__table__.insert(), history)
        db.execute(Job.__table__.insert(), jobs)
        db.execute(text('ANALYZE assets'))
        db.execute(text('ANALYZE jobs'))
        db.commit()

        queries = []

        def record(connection, cursor, statement, parameters, *args):
            if statement.startswith(('SELECT assets.', 'SELECT DISTINCT assets.')):
                queries.append((statement, parameters))

        event.listen(engine(), 'before_cursor_execute', record)
        try:
            cleanup(db)
        finally:
            event.remove(engine(), 'before_cursor_execute', record)
        assert db.get(Asset, orphan).purged_at
        assert db.get(Asset, pg['asset_id']).purged_at is None
        from app.storage import get_store
        assert get_store().exists(source['storage_key'])
        assert all(db.get(Asset, row['output_asset_id']).deleted_at is None for row in jobs[:20])
        assert len(queries) == 3
        plans = [db.connection().exec_driver_sql('EXPLAIN (ANALYZE, FORMAT JSON) ' + sql, params).scalar()
                 for sql, params in queries]
        assert 'ix_assets_live_created' in str(plans[0])
        assert all('ix_assets_unpurged' in str(plan) for plan in plans[1:])
        # PostgreSQL may hash-join a small, hot jobs table; reference lookups
        # must also have an indexed path when history no longer fits in cache.
        reference = db.execute(text('EXPLAIN (FORMAT JSON) SELECT 1 FROM jobs WHERE output_asset_id = :id'),
            {'id': jobs[0]['output_asset_id']}).scalar()
        assert 'ix_jobs_output_asset_id' in str(reference)


@pytest.mark.parametrize('state', ['ready', 'missing', 'other-instance', 'stale-heartbeat', 'stale-success', 'failed'])
def test_container_probe_matches_role_readiness(pg, monkeypatch, state):
    from app import health, healthcheck
    from app.config import settings
    from app.db import session_factory
    from app.health_models import ServiceHeartbeat

    # Keep the direct probe on the isolated database/schema supplied by pg.
    monkeypatch.setenv('CLUSTER_NODE_TIMEOUT_SECONDS', '30')
    settings.cache_clear()
    identity = healthcheck.instance_id(123)
    if state != 'missing':
        health.report_progress('maintenance', instance=identity if state != 'other-instance' else 'another:123')
        with session_factory()() as db:
            row = db.get(ServiceHeartbeat, ('maintenance', identity if state != 'other-instance' else 'another:123'))
            if state == 'stale-heartbeat':
                row.heartbeat_at -= timedelta(seconds=31)
            elif state == 'stale-success':
                row.last_success_at -= timedelta(seconds=31)
            elif state == 'failed':
                row.consecutive_failures = 1
            db.commit()
    assert healthcheck.ready('maintenance', 123) == health.readiness(role='maintenance', instance=identity)[1] == (state == 'ready')
