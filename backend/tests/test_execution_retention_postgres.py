"""The same retention cases run on an isolated PostgreSQL schema."""
import os
import pytest

pytestmark = pytest.mark.skipif(os.environ.get('RUN_POSTGRES_CONCURRENCY') != '1',
    reason='Requires the dedicated nodecomics_concurrency_test database')


def test_claim_cleanup_uses_retention_index_with_large_recent_history(client):
    from sqlalchemy import event, text
    from app.db import session_factory
    from app.execution_retention import cleanup_execution_data
    from app.models import now, uid
    from app.queue_models import ComputeClaim
    from test_execution_retention import seed, add_claim
    with session_factory()() as db:
        _, node, lease = seed(db, days=8)
        add_claim(db, node, [lease], 8)
        db.execute(ComputeClaim.__table__.insert(), [{'node_id': node, 'request_id': uid(),
            'request_hash': 'a' * 64, 'lease_ids': [lease], 'created_at': now()} for _ in range(50000)])
        db.execute(text('ANALYZE compute_claims'))
        connection, plans = db.connection(), []

        def explain(conn, cursor, statement, parameters, context, executemany):
            if statement.startswith('DELETE FROM compute_claims'):
                plans.append(conn.exec_driver_sql('EXPLAIN (FORMAT JSON) ' + statement, parameters).scalar())

        event.listen(connection, 'before_cursor_execute', explain)
        try:
            assert cleanup_execution_data(db)['claims'] == 1
        finally:
            event.remove(connection, 'before_cursor_execute', explain)
        assert 'ix_compute_claims_retention' in str(plans)


def test_claim_cleanup_does_not_scan_lease_history_per_receipt(client):
    from sqlalchemy import event, select, text
    from app.db import session_factory
    from app.execution_retention import delete_claims
    from app.models import now, uid
    from app.queue_models import ExecutionLease
    from app.system_settings import get_request_limits
    from test_execution_retention import seed, add_claim
    with session_factory()() as db:
        _, node, lease = seed(db, days=8)
        for _ in range(20):
            add_claim(db, node, [lease], 8)
        template = dict(db.execute(select(ExecutionLease.__table__).where(ExecutionLease.id == lease)).mappings().one())
        db.execute(ExecutionLease.__table__.insert(), [
            {**template, 'id': uid(), 'completed_at': now()} for _ in range(10000)])
        db.execute(text('ANALYZE execution_leases'))
        db.execute(text('ANALYZE compute_claims'))
        connection, queries = db.connection(), []

        def record(conn, cursor, statement, parameters, context, executemany):
            if statement.startswith('DELETE FROM compute_claims'):
                queries.append((statement, parameters))

        savepoint = db.begin_nested()
        event.listen(connection, 'before_cursor_execute', record)
        try:
            assert delete_claims(db, now(), get_request_limits(db), 20) == 20
        finally:
            event.remove(connection, 'before_cursor_execute', record)
            savepoint.rollback()
        sql, params = queries[0]
        plan = connection.exec_driver_sql('EXPLAIN (ANALYZE, FORMAT JSON) ' + sql, params).scalar()[0]

        def scanned_leases(node):
            if node.get('Relation Name') == 'execution_leases':
                yield node['Actual Loops'] * (node['Actual Rows'] + node.get('Rows Removed by Filter', 0))
            for child in node.get('Plans', ()):
                yield from scanned_leases(child)

        assert sum(scanned_leases(plan['Plan'])) <= 20, plan
