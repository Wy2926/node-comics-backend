"""Opt-in purchase isolation and concurrency on an isolated PostgreSQL schema."""
from concurrent.futures import ThreadPoolExecutor
from threading import Lock

from sqlalchemy import select
from test_postgres_concurrency import pytestmark
from test_billing_purchases import purchase, buy


def test_distinct_purchase_requests_reuse_one_pending_checkout(purchase, monkeypatch):
    from app import creem_client, stripe_client
    from app.billing_models import BillingCheckout, BillingCheckoutAlias, BillingOrder
    from app.db import session_factory
    state = purchase
    provider = creem_client if state['provider'] == 'creem' else stripe_client
    original = provider.call
    receipt_lock = Lock()

    def provider_call(*args, **kwargs):
        # Only the sequential IDs in the mock are serialized, not the requests
        # or their PostgreSQL transactions.
        with receipt_lock:
            return original(*args, **kwargs)

    monkeypatch.setattr(provider, 'call', provider_call)
    keys = [f'purchase-tab-{index}' for index in range(6)]
    with ThreadPoolExecutor(max_workers=4) as pool:
        responses = list(pool.map(lambda key: buy(state, key), keys))
    assert all(response.status_code == 200 or state['provider'] == 'creem' and response.status_code == 409
        and response.json()['error']['code'] == 'CREEM_CHECKOUT_UNCERTAIN' for response in responses), [r.text for r in responses]
    replays = [buy(state, key) for key in keys]
    assert all(response.status_code == 200 for response in replays), [r.text for r in replays]
    assert len({response.json()['checkout_id'] for response in replays}) == len(state['sessions']) == 1
    with session_factory()() as db:
        checkouts = list(db.scalars(select(BillingCheckout).where(BillingCheckout.owner_id == state['owner'])))
        orders = list(db.scalars(select(BillingOrder).where(BillingOrder.owner_id == state['owner'])))
        aliases = list(db.scalars(select(BillingCheckoutAlias).where(BillingCheckoutAlias.owner_id == state['owner'])))
        assert len(checkouts) == 1 and len(aliases) == 5
        assert {row.idempotency_key for row in checkouts + aliases} == set(keys)
        assert {alias.checkout_id for alias in aliases} == {checkouts[0].id}
        assert all(row.status == 'open' for row in checkouts)
        assert len(orders) == 1 and orders[0].status == 'pending'
    posts = len(state['posts'])
    for key, response in zip(keys, replays):
        assert buy(state, key).json() == response.json()
    assert len(state['posts']) == posts
    if state['provider'] == 'creem':
        assert posts == 1
