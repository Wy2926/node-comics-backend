import pytest
from sqlalchemy import select

from test_billing_prepaid import prepaid, membership_records
from test_billing_purchases import buy, notify, paid, purchase, records


def replay_taxed_payment(state, base, tax):
    from app.billing_models import BillingEvent, BillingPrice
    from app.billing_sync import process_event, sync_session
    from app.db import session_factory
    from app.models import now
    with session_factory()() as db:
        db.get(BillingPrice, 'purchase-price').unit_amount = base
        db.commit()
    state['product']['price'] = base
    checkout = buy(state).json()['checkout_id']
    session = paid(state, sync=False)
    session['product'] = {**state['product'], 'tax_mode': 'exclusive'}
    order = session['order']
    gross = base + tax
    order.update(amount=base, sub_total=base, tax_amount=tax, amount_due=gross, amount_paid=gross)
    tx = state['transactions'][order['transaction']]
    tx.update(amount=gross, amount_paid=base, tax_amount=tax)
    assert notify(state, 'checkout.completed', session).status_code == 200
    with session_factory()() as db:
        event = db.scalars(select(BillingEvent)).one()
        assert (event.status, event.error_code) == ('pending', 'CREEM_PURCHASE_MISMATCH')
        assert records()[0][0].status == 'processing' and not records()[1] and not membership_records()[1]
        event.next_attempt_at = now()
        db.commit()
    tx['amount_paid'] = gross
    process_event(event.id)
    sync_session(session['id'], provider='creem')
    with session_factory()() as db:
        assert db.get(BillingEvent, event.id).status == 'processed'
    replay = buy(state).json()
    assert replay['checkout_id'] == checkout and replay['fulfilled'] and len(state['posts']) == 1
    [order], _, _ = records()
    assert (order.status, order.subtotal, order.total, order.paid_at) == ('paid', base, gross, state['at'])


@pytest.mark.parametrize('purchase', ['creem'], indirect=True)
@pytest.mark.parametrize('prepaid', ['month'], indirect=True)
def test_taxed_month_card_releases_original_checkout(prepaid):
    replay_taxed_payment(prepaid, 399, 20)
    [order], [term], [bucket] = membership_records()
    assert term.billing_order_id == order.id and bucket.billing_term_id == term.id


@pytest.mark.parametrize('purchase', ['creem'], indirect=True)
def test_taxed_pack_releases_original_checkout(purchase):
    from app.billing_sync import sync_session
    replay_taxed_payment(purchase, 999, 50)
    [order], [bucket], [entry] = records()
    assert bucket.billing_order_id == order.id and entry.period_id == bucket.id
    assert buy(purchase, 'new-purchase').status_code == 200 and len(purchase['posts']) == 2
    session = next(iter(purchase['sessions'].values()))
    tx = purchase['transactions'][order.external_id]
    for refunded, status in [(999, 'partially_refunded'), (1049, 'refunded')]:
        tx.update(refunded_amount=refunded, status='partialRefund' if refunded == 999 else 'refunded')
        sync_session(session['id'], provider='creem')
        orders, [bucket], [entry] = records()
        original = next(row for row in orders if row.id == order.id)
        assert (original.status, original.total, original.refunded_total) == (status, 1049, refunded)
        assert (bucket.revoked_at is not None) == (refunded == 1049)


@pytest.mark.parametrize('order_change,tx_change', [
    ({}, {'amount': 420}), ({}, {'amount_paid': 420}), ({}, {'tax_amount': 19}),
    ({'sub_total': 398}, {}), ({'amount_due': 420}, {}), ({'amount_paid': 399}, {}),
    ({'tax_amount': -1}, {}), ({}, {'tax_amount': False}), ({}, {'amount': 419.0}),
])
def test_unexplained_or_malformed_amount_rejected(order_change, tx_change):
    from app.billing_providers import BillingError
    from app.billing_purchases import creem_purchase_total
    order = dict(sub_total=399, tax_amount=20, amount_due=419, amount_paid=419) | order_change
    tx = dict(amount=419, amount_paid=419, tax_amount=20) | tx_change
    with pytest.raises(BillingError, match='^CREEM_PURCHASE_MISMATCH$'):
        creem_purchase_total(order, tx, {'tax_mode': 'exclusive'}, 399)


def test_tax_included_in_price_is_not_added_twice():
    from app.billing_purchases import creem_purchase_total
    tx = dict(amount=399, amount_paid=399, tax_amount=20)
    assert creem_purchase_total({'sub_total': 379, 'tax_amount': 20}, tx, {'tax_mode': 'inclusive'}, 399) == 399
