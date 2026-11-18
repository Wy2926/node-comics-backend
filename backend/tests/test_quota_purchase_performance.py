"""Opt-in isolated permanent-purchase history benchmark; never touches live data."""
import json
import os
from statistics import median, quantiles
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from time import perf_counter
from types import SimpleNamespace
import tracemalloc

import pytest
from sqlalchemy import event, func, insert, or_, select, text

from app.billing_access import first_purchase
from app.billing_models import BillingOrder
from app.db import engine, session_factory
from app.entitlement_models import QuotaPeriod
from app.entitlements import admission_policy, entitlements_json
from app.models import User, now
from app.translation_selection import ModelAccess
from test_quota_purchase_admission import account, cluster, seed_purchase  # noqa: F401
from test_cluster_submissions import descriptor, submit
from conftest import configure_system_limits, login

def measure(function, count=15):
    durations = []
    for _ in range(count):
        start = perf_counter()
        function()
        durations.append((perf_counter() - start) * 1000)
    return round(median(durations), 3)


@pytest.mark.skipif(os.environ.get('RUN_QUOTA_SCALE') != '1', reason='Opt-in concurrent HTTP admission measurement')
def test_purchased_page_admission_across_accounts(client):
    from app.models import Ledger
    configure_system_limits(free_daily_pages=0)
    accounts = [login(client, f'pack-{i}') for i in range(8)]
    with session_factory()() as db:
        for auth in accounts:
            owner = client.get('/v1/me', headers=auth).json()['user']['id']
            seed_purchase(db, owner, pages=5)
        db.commit()
    barrier = Barrier(len(accounts))
    def run(auth):
        elapsed = []
        for i in range(5):
            barrier.wait(timeout=10)
            start = perf_counter()
            assert submit(client, auth, descriptor(f'page-{i}'.encode()), key=str(i)).status_code == 202
            elapsed.append((perf_counter() - start) * 1000)
        return elapsed
    with ThreadPoolExecutor(len(accounts)) as pool:
        elapsed = sorted(ms for owner in pool.map(run, accounts) for ms in owner)
    with session_factory()() as db:
        assert db.scalars(select(func.sum(Ledger.amount)).where(Ledger.kind == 'reserve').group_by(Ledger.owner_id)).all() == [5] * 8
    print(json.dumps({'parallel_accounts': len(accounts), 'requests': len(elapsed),
                      'http_p50_ms': round(median(elapsed), 1), 'http_p95_ms': round(quantiles(elapsed, n=20, method='inclusive')[-1], 1)}))


@pytest.mark.skipif(os.environ.get('RUN_QUOTA_SCALE') != '1',
    reason='Opt-in isolated purchase-history latency and memory measurement')
@pytest.mark.parametrize('history_size', [0, 50000])
def test_permanent_history_never_loads_at_admission_or_into_account_response(account, history_size):
    owner = account[2]
    with session_factory()() as db:
        active = seed_purchase(db, owner, pages=1)
        order = db.get(BillingOrder, active.billing_order_id)
        for offset in range(0, history_size, 1000):
            indexes = range(offset, min(history_size, offset + 1000))
            db.execute(insert(BillingOrder), [{'id': f'benchmark-order-{index:020}', 'owner_id': owner,
                'provider': 'stripe', 'environment': 'test', 'price_id': order.price_id, 'binding_id': order.binding_id,
                'external_id': f'benchmark-payment-{index}', 'kind': 'initial', 'status': 'paid', 'currency': 'usd',
                'total': 599, 'paid_at': active.starts_at} for index in indexes])
            db.execute(insert(QuotaPeriod), [{'id': f'benchmark-period-{index:020}', 'owner_id': owner,
                'billing_order_id': f'benchmark-order-{index:020}', 'kind': 'classic_purchase', 'mode': 'classic',
                'source': 'purchase', 'source_key': f'purchase:benchmark-order-{index}:classic',
                'starts_at': active.starts_at, 'granted': 1, 'used': 1, 'reserved': 0} for index in indexes])
        db.commit()
        active_id = active.id
        db.execute(text('ANALYZE'))
        db.commit()
    statements = []
    def record(connection, cursor, statement, parameters, context, many):
        if statement.lstrip().startswith('SELECT'):
            statements.append((statement, parameters))
    at = now()
    with session_factory()() as db:
        user = db.get(User, owner)
        first_purchase(db, owner, at)  # Compile/warm up before measuring.
        event.listen(engine(), 'before_cursor_execute', record)
        try:
            policy = admission_policy(db, user, at=at)
            admission_reads = len(statements)
            access = ModelAccess(db, user, at)
            assert access.paid_service() == 'lite'
            eligibility_reads = len(statements)
            scoped, reason = access.resolve(SimpleNamespace(text_plan_ids=['lite'], quota_multiplier=1))
            assert reason is None and scoped.period.id == active_id
        finally:
            event.remove(engine(), 'before_cursor_execute', record)
        assert policy.period.id == active_id
        for reads in (statements[:admission_reads], statements[admission_reads:eligibility_reads], statements[eligibility_reads:]):
            sql, params = next((sql, params) for sql, params in reads if 'JOIN billing_orders' in sql)
            assert 'LIMIT' in sql or 'EXISTS' in sql
            if history_size:
                plan = db.connection().exec_driver_sql('EXPLAIN (FORMAT JSON) ' + sql, params).scalar_one()
                assert 'ix_quota_purchase_available' in str(plan), plan
        select_ms = measure(lambda: admission_policy(db, user, at=at))
        summary_ms = measure(lambda: entitlements_json(db, user, at), count=5)
        payload = entitlements_json(db, user, at)
        assert payload['purchase_quota']['granted'] == history_size + 1
        assert payload['purchase_quota']['available'] == 1
        assert not any(row['source'] == 'purchase' for row in payload['modes']['classic']['quota']['buckets'])
        payload_bytes = len(json.dumps(payload).encode())
        assert payload_bytes < 5000

    def bounded():
        with session_factory()() as db:
            admission_policy(db, db.get(User, owner), at=at)
    def previous_full_load():
        with session_factory()() as db:
            periods = list(db.scalars(select(QuotaPeriod).where(QuotaPeriod.owner_id == owner,
                QuotaPeriod.mode == 'classic', QuotaPeriod.starts_at <= at,
                or_(QuotaPeriod.ends_at.is_(None), QuotaPeriod.ends_at > at))))
            return next(row.id for row in sorted(periods, key=lambda row: (row.starts_at, row.id))
                if row.granted > row.used + row.reserved)
    legacy_ms = measure(previous_full_load, count=3)
    peak_kib = {}
    for name, function in [('selector', bounded), ('legacy_full_load', previous_full_load)]:
        tracemalloc.start()
        function()
        peak_kib[name] = round(tracemalloc.get_traced_memory()[1] / 1024, 1)
        tracemalloc.stop()

    with session_factory()() as db:
        db.get(QuotaPeriod, active_id).used = 1
        db.commit()
        user = db.get(User, owner)
        assert ModelAccess(db, user, at).paid_service() is None
        fallback_statements = []
        def record_fallback(connection, cursor, statement, parameters, context, many):
            if 'source !=' in statement:
                fallback_statements.append((statement, parameters))
        event.listen(engine(), 'before_cursor_execute', record_fallback)
        try:
            assert admission_policy(db, user, at=at).service_plan == 'free'
        finally:
            event.remove(engine(), 'before_cursor_execute', record_fallback)
        if history_size:
            sql, params = fallback_statements[0]
            plan = db.connection().exec_driver_sql('EXPLAIN (FORMAT JSON) ' + sql, params).scalar_one()
            assert 'ix_quota_nonpurchase' in json.dumps(plan), plan
        fallback_ms = measure(lambda: admission_policy(db, user, at=at))
    report = {'engine': engine().dialect.name, 'depleted_permanent_buckets': history_size,
        'policy_p50_ms': select_ms, 'free_fallback_p50_ms': fallback_ms, 'summary_p50_ms': summary_ms,
        'legacy_full_load_p50_ms': legacy_ms, 'peak_kib': peak_kib, 'entitlements_bytes': payload_bytes,
        'policy_select_statements': admission_reads}
    print(json.dumps(report))
    if history_size:
        assert select_ms < legacy_ms / 5
        assert peak_kib['selector'] < peak_kib['legacy_full_load'] / 20
