"""Authenticated multi-channel checkout and signature-verified durable webhooks."""
import hashlib
import hmac
import json
import re
import secrets
from typing import Literal
import stripe as sdk
from fastapi import APIRouter, BackgroundTasks, Depends, Query, Request
from fastapi.security import HTTPAuthorizationCredentials
from fastapi.security.http import HTTPBase
from fastapi.responses import JSONResponse
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session
from . import stripe_client as stripe, creem_client as creem
from .auth import bearer, identity
from . import redis_state
from .billing_checkout import billing_status, start_checkout, sync_owner, customer_for, current_subscription
from .billing_models import BillingEvent
from .billing_providers import BillingError, require, provider_enabled, provider_environment, resource_key
from .billing_sync import process_event
from .billing_grants import timestamp
from .request_models import RequestBody
from pydantic import BaseModel, Field
from .billing_catalog import offers, products_json
from .config import settings
from .db import get_db
from .entitlements import entitlements_json
from .errors import problem
from .models import User

router = APIRouter(tags=['billing'])


class CheckoutRequest(RequestBody):
    price_id: str = Field(min_length=1, max_length=36)
    provider: Literal['stripe', 'creem']


class PortalRequest(RequestBody):
    provider: Literal['stripe', 'creem']


STRIPE_EVENTS = {'checkout.session.completed', 'checkout.session.expired', 'checkout.session.async_payment_succeeded',
    'checkout.session.async_payment_failed', 'customer.subscription.created', 'customer.subscription.updated',
    'customer.subscription.deleted', 'customer.subscription.paused', 'customer.subscription.resumed',
    'invoice.paid', 'invoice.payment_failed', 'invoice.payment_action_required', 'invoice.voided', 'invoice.marked_uncollectible',
    'charge.refunded', 'charge.dispute.created', 'charge.dispute.closed', 'refund.updated', 'refund.created'}
CREEM_EVENTS = {'checkout.completed', 'subscription.active', 'subscription.paid', 'subscription.canceled',
    'subscription.expired', 'subscription.trialing', 'subscription.paused', 'subscription.update',
    'subscription.past_due', 'subscription.unpaid', 'subscription.scheduled_cancel', 'refund.created', 'dispute.created'}


def billing_error(exc):
    messages = {'BILLING_SUBSCRIPTION_EXISTS': '已有订阅，请管理当前订阅',
        'BILLING_PREPAID_ACTIVE': '会员卡尚未到期，可以手动续购',
        'BILLING_GIFT_ACTIVE': '赠送会员期间不会扣款，请在赠送结束后购买会员卡',
        'SUBSCRIPTION_NOT_FOUND': '没有可管理的订阅',
        'BILLING_CHECKOUT_UNCERTAIN': '正在核实原结账结果，请稍后刷新',
        'CREEM_CHECKOUT_UNCERTAIN': '此笔结账请求结果待核实，不会重复下单，请联系支持恢复原订单',
        'BILLING_IDEMPOTENCY_KEY_REQUIRED': '一次性购买需要原购买操作编号，请刷新后重试',
        'BILLING_IDEMPOTENCY_KEY_INVALID': '购买操作编号无效，请刷新后重试',
        'BILLING_PURCHASE_PROCESSING': '正在核实此笔支付，请稍后刷新，不会重复下单',
        'BILLING_PAYMENT_TIME_PENDING': '正在核实原付款时间，请稍后刷新',
        'BILLING_PURCHASE_RETRY_ALLOWED': '原购买已确认结束且未付款，可以手动重新购买',
        'BILLING_CHECKOUT_COMPLETED': '结账已完成，请刷新会员权益',
        'BILLING_CHECKOUT_PRICE_CONFLICT': '此购买意图已有待核实结账，请继续原价格与支付渠道',
        'BILLING_CHECKOUT_PENDING': '请先完成或核实已有待付款会员订单，再发起新购买',
        'BILLING_PLAN_UNAVAILABLE': '此价格尚未发布或已停售，请刷新套餐列表',
        'BILLING_CHANNEL_UNAVAILABLE': '此支付渠道暂不可用，请刷新套餐列表',
        'BILLING_CHECKOUT_EXPIRED': '结账链接已过期，请重新打开', 'BILLING_DISABLED': '支付渠道暂不可用'}
    problem(exc.code, messages.get(exc.code, '支付服务暂时不可用，请稍后重试'), 503 if exc.uncertain else 409)


class AppHandoff(RequestBody):
    code: str = Field(pattern=r'^[A-Za-z0-9_-]{43}$', max_length=43)


class AppHandoffResponse(BaseModel):
    account_id: str
    code: str


class AppBillingSession(BaseModel):
    account_id: str
    session: str


app_checkout = HTTPBase(scheme='Billing', scheme_name='AppCheckout', auto_error=False,
    description='Tab-scoped authorization for billing status and checkout only.')


def app_key(kind, secret):
    return redis_state.key('billing-' + kind, hashlib.sha256(secret.encode()).hexdigest())


@router.post('/v1/billing/app-handoff', response_model=AppHandoffResponse)
def issue_app_handoff(user: User = Depends(identity)):
    """Issue a single-use pricing link code for the authenticated account (60 seconds)."""
    code = secrets.token_urlsafe(32)
    window = redis_state.window('billing-link-issue', user.id, 12, member=hashlib.sha256(code.encode()).hexdigest())
    if window['retry_after_seconds']:
        problem('RATE_LIMITED', '请稍后再打开升级页面', 429, retry_after_seconds=window['retry_after_seconds'])
    redis_state.command('set', app_key('handoff', code), user.id, ex=60)
    return {'code': code, 'account_id': user.id}


@router.post('/v1/billing/app-handoff/redeem', response_model=AppBillingSession)
def redeem_app_handoff(body: AppHandoff):
    """Exchange the app code once for a 15-minute billing-only session."""
    owner = redis_state.command('getdel', app_key('handoff', body.code))
    if not owner:
        problem('BILLING_LINK_EXPIRED', '请返回 App 重新打开升级页面', 401)
    token = secrets.token_urlsafe(32)
    redis_state.command('set', app_key('session', token), owner, ex=15 * 60)
    return {'account_id': owner, 'session': token}


def billing_identity(request: Request, credentials: HTTPAuthorizationCredentials | None = Depends(bearer),
        checkout: HTTPAuthorizationCredentials | None = Depends(app_checkout), db: Session = Depends(get_db)):
    if not checkout or checkout.scheme.lower() != 'billing':
        return identity(credentials, db)
    token = checkout.credentials
    owner = redis_state.command('get', app_key('session', token)) if re.fullmatch(r'[A-Za-z0-9_-]{43}', token) else None
    user = db.get(User, owner) if owner else None
    if not user or user.kind != 'registered':
        problem('BILLING_LINK_EXPIRED', '请返回 App 重新打开升级页面', 401)
    request.state.app_pricing = True
    return user


@router.get('/v1/billing/status')
def status(price_id: str | None = Query(None, min_length=1, max_length=36),
        user: User = Depends(billing_identity), db: Session = Depends(get_db)):
    return billing_status(db, user, price_id)


@router.get('/v1/billing/catalog')
def public_catalog(db: Session = Depends(get_db)):
    enabled = any(provider_enabled(p) for p in ('stripe', 'creem'))
    subscription_offers = offers(db) if enabled else []
    return {'enabled': enabled, 'offers': subscription_offers,
        'quota_offers': offers(db, interval='once') if enabled else [],
        'products': products_json(db, public_offers=subscription_offers) if enabled else []}


@router.post('/v1/billing/checkouts')
def checkout(body: CheckoutRequest, request: Request, user: User = Depends(billing_identity)):
    try:
        return start_checkout(user.id, body.price_id, body.provider, request.headers.get('Idempotency-Key'),
            app_pricing=getattr(request.state, 'app_pricing', False))
    except BillingError as exc:
        billing_error(exc)


@router.post('/v1/billing/sync')
def sync(user: User = Depends(identity), db: Session = Depends(get_db)):
    try:
        sync_owner(user.id)
        from .billing_renewal import process_owner
        process_owner(user.id)
    except BillingError as exc:
        billing_error(exc)
    db.expire_all()
    return {'billing': billing_status(db, user), 'entitlements': entitlements_json(db, user)}


@router.post('/v1/billing/cancel-renewal')
def cancel_renewal(body: PortalRequest, user: User = Depends(identity), db: Session = Depends(get_db)):
    from .billing_renewal import cancel_renewal as cancel
    try:
        cancel(user.id, body.provider)
    except BillingError as exc:
        billing_error(exc)
    db.expire_all()
    return billing_status(db, user)


@router.post('/v1/billing/portal')
def portal(body: PortalRequest, user: User = Depends(identity), db: Session = Depends(get_db)):
    account = current_subscription(db, user.id, body.provider) or customer_for(db, user.id, body.provider)
    if not account:
        problem('SUBSCRIPTION_NOT_FOUND', '没有可管理的订阅', 404)
    try:
        require(provider_enabled(body.provider), 'BILLING_DISABLED')
        if body.provider == 'creem':
            result = creem.call('POST', '/customers/billing', body={'customer_id': account.customer_id})
            return {'url': creem.hosted_url(result['customer_portal_link'], portal=True), 'provider': 'creem'}
        result = stripe.call('billing_portal.sessions', 'create', params={
            'customer': account.customer_id, 'return_url': settings().stripe_return_url,
            **({'configuration': settings().stripe_portal_configuration_id} if settings().stripe_portal_configuration_id else {})})
        return {'url': stripe.hosted_url(result['url'], portal=True), 'provider': 'stripe'}
    except BillingError as exc:
        billing_error(exc)


async def read_body(request):
    body = bytearray()
    async for chunk in request.stream():
        if len(body) + len(chunk) > 1024 * 1024:
            return None
        body.extend(chunk)
    return bytes(body)


def store_event(db, tasks, provider, event_id, kind, resource_id, occurred, payload):
    key = resource_key(provider, event_id)
    if not db.get(BillingEvent, key):
        db.add(BillingEvent(id=key, provider=provider, environment=provider_environment(provider), event_type=kind,
            resource_id=resource_id, occurred_at=occurred, payload=payload))
        try:
            db.commit()
        except IntegrityError:
            db.rollback()
    tasks.add_task(process_event, key)
    return {'received': True}


def event_references(obj):
    """Keep only validated correlation IDs, never customer or raw event fields."""
    metadata = obj.get('metadata')
    metadata = metadata if isinstance(metadata, dict) else {}
    parent = obj.get('parent')
    parent = parent if isinstance(parent, dict) else {}
    details = parent.get('subscription_details')
    details = details if isinstance(details, dict) else {}
    checkout = obj.get('checkout') if isinstance(obj.get('checkout'), dict) else {}
    values = {'checkout_id': metadata.get('checkout_intent_id') if metadata.get('app') == 'node_comics' else None,
        'subscription_id': obj.get('subscription') or details.get('subscription'),
        'checkout_session_id': checkout.get('id'),
        'invoice_id': obj.get('invoice'), 'transaction_id': obj.get('transaction') or obj.get('payment_intent') or (
            obj.get('order', {}).get('transaction') if isinstance(obj.get('order'), dict) else None),
        'refund_id': obj.get('id') if obj.get('object') == 'refund' else None,
        'dispute_id': obj.get('id') if obj.get('object') == 'dispute' else None,
        'charge_id': obj.get('charge') or (obj.get('id') if obj.get('object') == 'charge' else None)}
    references = {}
    for key, value in values.items():
        if isinstance(value, dict):
            value = value.get('id')
        if isinstance(value, str) and re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_-]{0,254}', value):
            references[key] = value
    return references


def creem_reversal_snapshot(obj, kind):
    """Only signed, typed financial fields; never persist customer data or evidence."""
    amount = obj.get('refund_amount' if kind == 'refund.created' else 'amount')
    currency = obj.get('refund_currency' if kind == 'refund.created' else 'currency')
    status = obj.get('status')
    require(amount is None or type(amount) is int and amount >= 0, 'CREEM_REVERSAL_INVALID')
    require(currency is None or isinstance(currency, str) and re.fullmatch(r'[A-Za-z]{3}', currency), 'CREEM_REVERSAL_INVALID')
    require(status is None or isinstance(status, str) and re.fullmatch(r'[A-Za-z0-9_-]{1,40}', status), 'CREEM_REVERSAL_INVALID')
    return {'amount': amount, 'currency': currency.lower() if currency else None, 'status': status or 'unknown'}


@router.post('/webhooks/stripe', include_in_schema=False)
async def stripe_webhook(request: Request, tasks: BackgroundTasks, db: Session = Depends(get_db)):
    if not provider_enabled('stripe'):
        return JSONResponse({'error': 'billing_disabled'}, status_code=503)
    body = await read_body(request)
    if body is None:
        return JSONResponse({'error': 'payload_too_large'}, status_code=413)
    try:
        value = sdk.Webhook.construct_event(body, request.headers.get('Stripe-Signature', ''),
            settings().stripe_webhook_secret.get_secret_value()).to_dict()
    except (ValueError, TypeError, AttributeError, sdk.SignatureVerificationError):
        return JSONResponse({'error': 'invalid_signature'}, status_code=401)
    try:
        require(isinstance(value, dict))
        stripe.environment(value)
        require(isinstance(value.get('data'), dict) and isinstance(value['data'].get('object'), dict))
        event_id, kind, obj = value['id'], value['type'], value['data']['object']
        resource_id = obj['id']
        require(re.fullmatch(r'evt_[A-Za-z0-9]{1,240}', event_id) and isinstance(kind, str))
        if kind not in STRIPE_EVENTS:
            return {'received': True}
        require(re.fullmatch(r'[A-Za-z][A-Za-z0-9_]{1,254}', resource_id))
        occurred = timestamp(value['created'])
        require(occurred)
        payload = event_references(obj)
    except (KeyError, TypeError, ValueError, BillingError):
        return JSONResponse({'error': 'invalid_event'}, status_code=400)
    return store_event(db, tasks, 'stripe', event_id, kind, resource_id, occurred, payload)


@router.post('/webhooks/creem', include_in_schema=False)
async def creem_webhook(request: Request, tasks: BackgroundTasks, db: Session = Depends(get_db)):
    if not provider_enabled('creem'):
        return JSONResponse({'error': 'billing_disabled'}, status_code=503)
    body = await read_body(request)
    if body is None:
        return JSONResponse({'error': 'payload_too_large'}, status_code=413)
    expected = hmac.new(settings().creem_webhook_secret.get_secret_value().encode(), body, hashlib.sha256).hexdigest()
    signature = request.headers.get('creem-signature', '')
    if not re.fullmatch(r'[a-fA-F0-9]{64}', signature) or not hmac.compare_digest(expected, signature.lower()):
        return JSONResponse({'error': 'invalid_signature'}, status_code=401)
    try:
        value = json.loads(body)
        require(isinstance(value, dict) and isinstance(value.get('object'), dict))
        event_id, kind, obj = value['id'], value['eventType'], value['object']
        require(re.fullmatch(r'evt_[A-Za-z0-9]{1,240}', event_id) and isinstance(kind, str))
        if kind not in CREEM_EVENTS:
            return {'received': True}
        creem.environment(obj)
        resource_id = obj['id']
        require(re.fullmatch(r'[A-Za-z][A-Za-z0-9_]{1,254}', resource_id))
        occurred = creem.timestamp(value['created_at'])
        require(occurred)
        payload = event_references(obj)
        if kind in ('refund.created', 'dispute.created'):
            payload['reversal'] = creem_reversal_snapshot(obj, kind)
    except (KeyError, TypeError, ValueError, BillingError):
        return JSONResponse({'error': 'invalid_event'}, status_code=400)
    return store_event(db, tasks, 'creem', event_id, kind, resource_id, occurred, payload)
