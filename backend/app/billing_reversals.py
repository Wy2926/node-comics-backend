"""Refund/dispute observations under the owning account lock; no money-moving APIs."""
import re
from sqlalchemy import func, literal_column, select
from .billing_models import BillingEvent, BillingRefund, BillingDispute, BillingOrderTransition
from .billing_orders import transition
from .billing_providers import provider_environment, require
from .models import now


def record_reversal(db, order, kind, external_id, *, amount, currency, status, occurred_at, observed_at, event_id=None):
    require(kind in ('refund', 'dispute') and isinstance(external_id, str)
        and re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_-]{0,254}', external_id), 'BILLING_REVERSAL_INVALID')
    require(amount is None or type(amount) is int and amount >= 0, 'BILLING_REVERSAL_INVALID')
    require(currency is None or currency == order.currency, 'BILLING_REVERSAL_CURRENCY_MISMATCH')
    require(isinstance(status, str) and re.fullmatch(r'[A-Za-z0-9_-]{1,40}', status), 'BILLING_REVERSAL_INVALID')
    model = BillingRefund if kind == 'refund' else BillingDispute
    row = db.scalar(select(model).where(model.provider == order.provider, model.environment == order.environment,
        model.external_id == external_id))
    if row:
        require(row.order_id == order.id and row.transaction_id == order.external_id, 'BILLING_REVERSAL_CONFLICT')
        if observed_at < row.observed_at:
            return row
        require(row.amount is None or amount is None or row.amount == amount, 'BILLING_REVERSAL_AMOUNT_MISMATCH')
    else:
        row = model(order_id=order.id, provider=order.provider, environment=order.environment,
            external_id=external_id, transaction_id=order.external_id, occurred_at=occurred_at)
        db.add(row)
    before = (row.amount, row.currency, row.status)
    row.amount, row.currency = amount if amount is not None else row.amount, currency or row.currency
    # Sparse or delayed notices may not erase already verified values.
    if (status != 'unknown' or not row.status) and not (kind == 'refund' and row.status == 'succeeded' and status != 'succeeded'):
        row.status = status
    row.event_id, row.observed_at, row.synced_at = event_id or row.event_id, observed_at, now()
    if before != (row.amount, row.currency, row.status):
        db.add(BillingOrderTransition(order_id=order.id, source=kind + '_record', event_id=event_id,
            from_status=order.status, to_status=order.status,
            detail={'external_id': external_id, 'amount': row.amount, 'currency': row.currency, 'status': row.status}))
    return row


def reversal_json(row):
    return {key: getattr(row, key) for key in ('id', 'order_id', 'provider', 'environment', 'external_id',
        'transaction_id', 'amount', 'currency', 'status', 'event_id', 'occurred_at', 'observed_at', 'synced_at')}


def creem_transaction_events(transaction_id):
    return select(BillingEvent).where(BillingEvent.provider == 'creem',
        BillingEvent.environment == provider_environment('creem'),
        BillingEvent.payload.op('->>')(literal_column("'transaction_id'")) == transaction_id)


def apply_creem_reversals(db, order):
    # Signed reversals can precede settlement or a stale payment API snapshot.
    events = db.scalars(creem_transaction_events(order.external_id).where(
        BillingEvent.event_type.in_(['refund.created', 'dispute.created'])).order_by(BillingEvent.occurred_at))
    for event in events:
        snapshot = event.payload.get('reversal') or {}
        record_reversal(db, order, event.event_type.split('.')[0], event.resource_id,
            amount=snapshot.get('amount'), currency=snapshot.get('currency'), status=snapshot.get('status') or 'unknown',
            occurred_at=event.occurred_at, observed_at=event.occurred_at, event_id=event.id)
        if event.event_type == 'dispute.created':
            transition(db, order, 'disputed', 'purchase_reversal', event.id)
    db.flush()
    total = db.scalar(select(func.coalesce(func.sum(BillingRefund.amount), 0)).where(
        BillingRefund.order_id == order.id, BillingRefund.status == 'succeeded', BillingRefund.currency == order.currency))
    order.refunded_total = max(order.refunded_total or 0, total)
    if order.status not in ('refunded', 'disputed') and order.refunded_total:
        # A partial-refund snapshot may report net payment; it alone cannot prove a full refund.
        amount_due = max(order.total, order.subtotal or 0) if order.status == 'partially_refunded' else order.total
        transition(db, order, 'refunded' if amount_due and order.refunded_total >= amount_due else 'partially_refunded', 'purchase_reversal')
