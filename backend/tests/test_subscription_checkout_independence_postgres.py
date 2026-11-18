"""Real database locking with simulated Stripe/Creem, never a payment account."""
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier

from test_postgres_concurrency import pytestmark
from test_stripe_billing import billing
from test_creem_billing import creem_billing
from test_subscription_checkout_independence import buyer, checkout


def test_concurrent_choices_keep_one_intent_per_price(buyer):
    state = buyer
    ready = Barrier(4)
    prices = [state['price_id'], state['other_price']] * 2

    def create(price):
        ready.wait(timeout=10)
        return checkout(state, price)

    with ThreadPoolExecutor(max_workers=4) as pool:
        results = list(pool.map(create, prices))
    for response in results:
        assert response.status_code == 200 or response.json()['error']['code'] == 'CREEM_CHECKOUT_UNCERTAIN', response.text
    assert len(state['sessions']) == 2
    from app.billing_models import BillingCheckout, BillingOrder
    from app.db import session_factory
    from sqlalchemy import func, select
    with session_factory()() as db:
        assert db.scalar(select(func.count()).select_from(BillingCheckout)) == 2
        assert db.scalar(select(func.count()).select_from(BillingOrder)) == 2
    for price in set(prices):
        result = checkout(state, price)
        assert result.status_code == 200, result.text
    if state['provider'] == 'creem':
        assert len(state['posts']) == 2
    else:
        assert len({key for key, _ in state['posts']}) == 2
