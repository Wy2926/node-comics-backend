"""Simultaneous administrator updates in isolated PostgreSQL."""
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from test_postgres_concurrency import pytestmark
from test_creem_billing import creem_billing
from test_billing_default_provider import enable_stripe


def test_concurrent_admin_updates_leave_exactly_one_default(creem_billing, monkeypatch):
    state = creem_billing
    enable_stripe(state, monkeypatch)
    ready = Barrier(2)

    def update(provider):
        ready.wait(timeout=10)
        return state['client'].put('/v1/admin/billing/default-provider', headers=state['auth'],
            json={'provider':provider}).status_code

    with ThreadPoolExecutor(max_workers=2) as pool:
        assert list(pool.map(update, ('stripe','creem'))) == [200,200]
    catalog = state['client'].get('/v1/admin/billing/catalog', headers=state['auth']).json()
    public = state['client'].get('/v1/billing/catalog').json()['offers']
    assert public
    assert all([c['provider'] for c in p['channels']] == [catalog['default_provider']] for p in public)
