"""Current public products, quarterly grants and no-trial payment contracts."""
from datetime import datetime, timedelta
from types import SimpleNamespace
import pytest
from sqlalchemy import select, update
from conftest import login
from app.billing_models import BillingCheckout, BillingPlanRevision, BillingPrice, BillingPriceBinding, BillingSubscription, BillingSettings
from app.db import session_factory
from app.entitlement_models import QuotaPeriod
from app.models import User
from test_document_quota_api import document_case  # noqa: F401


@pytest.fixture
def catalog_products():
    return None  # Exercise the real public seed, not the protocol tests' sample catalog.


def test_current_catalog_is_finite_no_trial_and_packs_never_expire(client):
    from app.billing_catalog import initialize_catalog
    with session_factory()() as db:
        initialize_catalog(db)
        assert {(p.id, p.unit_amount) for p in db.scalars(select(BillingPrice))} == {
            ('plus-month-prepaid-v1', 299), ('pro-month-prepaid-v1', 399),
            ('plus-quarter-prepaid-v1', 666), ('plus-year-prepaid-v1', 2399),
            ('pro-quarter-prepaid-v1', 999), ('pro-year-prepaid-v1', 3599),
            ('pages-3500-once-v1', 599), ('pages-7000-once-v1', 999)}
        for plan, pages in [('plus', 2500), ('pro', 4000)]:
            revision = db.get(BillingPlanRevision, plan + '-v1')
            assert revision.monthly_classic_pages == pages
            assert (revision.trial_days, revision.trial_classic_pages) == (0, 0)
        for pages in (3500, 7000):
            revision = db.get(BillingPlanRevision, f'pages-{pages}-v1')
            assert (revision.quota_pages, revision.quota_validity_days, revision.service_plan_id) == (pages, None, 'plus')
        for price in db.scalars(select(BillingPrice)):
            assert price.status == 'draft'
            assert price.billing_type == 'onetime'


def test_catalog_has_no_payment_preference_and_service_labels_remain_dynamic(client):
    from app.billing_catalog import price_json
    from app.billing_models import BillingPlan
    catalog = client.get('/v1/billing/catalog').json()
    auth = login(client, 'catalog-default-reader')
    status = client.get('/v1/billing/status', headers=auth).json()
    assert 'checkout_defaults' not in catalog and 'checkout_defaults' not in status
    with session_factory()() as db:
        db.get(BillingPlan, 'plus').name = 'Renamed membership'
        quote = price_json(db, db.get(BillingPrice, 'pages-3500-once-v1'))
        assert quote['service_plan_name'] == 'Renamed membership'


@pytest.mark.parametrize('document_case', ['unsubscribed'], indirect=True)
@pytest.mark.parametrize('pages,filename,kind', [(3500, 'book.pdf', 'pdf'), (7000, 'book.cbz', 'zip')])
def test_existing_page_pack_serves_images_and_books_from_its_original_quote(document_case, pages, filename, kind):
    from app.billing_models import BillingOrder
    from app.billing_purchases import grant_purchase
    from app.models import Ledger, now
    from app.translation_models import TranslationProvider
    from app.documents.models import DocumentQuotaAllocation
    from test_cluster_submissions import descriptor, submit
    from test_document_quota_api import create, prepare, reserve
    from translation_fixtures import configure_text_provider
    case = document_case
    pack = f'pages-{pages}'
    with session_factory()() as db:
        price = db.get(BillingPrice, pack + '-once-v1')
        binding = BillingPriceBinding(price_id=price.id, provider='stripe', environment='test',
            product_id='prod_pack', provider_price_id='price_pack', status='draft')
        db.add(binding)
        db.flush()
        at = now() - timedelta(days=90)
        order = BillingOrder(owner_id=case.owner, provider='stripe', environment='test', price_id=price.id,
            binding_id=binding.id, kind='initial', status='paid', currency=price.currency, total=price.unit_amount, paid_at=at)
        db.add(order)
        db.flush()
        grant_purchase(db, SimpleNamespace(owner_id=case.owner, price_id=price.id, trial=False), order, at)
        bucket = db.scalar(select(QuotaPeriod).where(QuotaPeriod.billing_order_id == order.id))
        bucket.used, price.status = pages - 3, 'archived'
        # Later sales of the same product use a different tier.
        db.add(BillingPlanRevision(plan_id=pack, version=2, name='New quote', service_plan_id='pro',
            quota_pages=pages, monthly_classic_pages=0, trial_days=0, trial_classic_pages=0))
        db.get(TranslationProvider, case.provider).text_plan_ids = ['plus']
        exact = configure_text_provider(db, document={'enabled': True})
        exact.text_plan_ids = [pack]
        other = configure_text_provider(db, document={'enabled': True})
        other.text_plan_ids = ['pro', 'pages-7000' if pages == 3500 else 'pages-3500']
        db.commit()
        exact_id, period_id = exact.id, bucket.id
    models = case.client.get('/v1/capabilities', headers=case.auth).json()['translation_models']
    books = case.client.get('/v1/documents/capabilities', headers=case.auth).json()
    assert books['enabled'] and {item['id'] for item in books['models']} == {case.provider, exact_id}
    assert {item['id'] for item in models if item['available']} == {case.provider, exact_id}
    assert case.client.get('/v1/me/entitlements', headers=case.auth).json()['plan'] == 'free'
    assert submit(case.client, case.auth, descriptor(b'page'), model_id=exact_id).status_code == 202
    key = create(case, filename=filename, source_type=kind, model_id=exact_id)
    job_id = prepare(case, key, 2)
    assert reserve(job_id) and reserve(job_id)
    assert not case.client.get('/v1/documents/capabilities', headers=case.auth).json()['enabled']
    assert create(case, key, filename=filename, source_type=kind, model_id=exact_id) == key and len(case.store.created) == 1
    with session_factory()() as db:
        bucket = db.get(QuotaPeriod, period_id)
        assert (bucket.granted, bucket.used, bucket.reserved) == (pages, pages - 3, 3)
        assert db.scalar(select(DocumentQuotaAllocation.period_id)) == period_id
        assert db.scalars(select(Ledger.amount).where(Ledger.kind == 'purchase')).all() == [pages]


@pytest.mark.parametrize('plan,pages', [('plus', 2500), ('pro', 4000)])
@pytest.mark.parametrize('interval,count,end', [('month', 1, datetime(2026, 2, 28)),
    ('quarter', 3, datetime(2026, 4, 30)), ('year', 12, datetime(2027, 1, 31))])
def test_paid_periods_grant_monthly_without_early_access_or_duplicates(client, plan, pages, interval, count, end):
    from app.billing_grants import grant_term
    from app.billing_access import first_subscription
    auth = login(client, 'catalog-reader')
    owner = client.get('/v1/me', headers=auth).json()['user']['id']
    start = datetime(2026, 1, 31)
    with session_factory()() as db:
        current = db.get(BillingPrice, f'{plan}-{interval}-prepaid-v1')
        price = BillingPrice(id='legacy-price', plan_id=plan, plan_revision_id=current.plan_revision_id,
            environment='test', currency=current.currency, unit_amount=current.unit_amount,
            interval=interval, billing_type='recurring', status='archived')
        db.add(price)
        db.flush()
        binding = BillingPriceBinding(id='grant-binding', price_id=price.id, provider='stripe',
            environment='test', product_id='prod_fixture', provider_price_id='price_fixture', status='draft')
        db.add(binding)
        db.flush()
        db.add(BillingCheckout(id='grant-checkout', owner_id=owner, provider='stripe', environment='test',
            price_id=price.id, binding_id=binding.id, return_url='https://comics.example/account/',
            status='completed', trial=False, expires_at=start))
        db.flush()
        sub = BillingSubscription(id='stripe:test:sub_grant', owner_id=owner, provider='stripe',
            checkout_id='grant-checkout', environment='test', customer_id='cus_fixture',
            price_id=price.id, binding_id=binding.id, status='active')
        db.add(sub)
        db.flush()
        for _ in range(2):
            grant_term(db, db.get(User, owner), sub, price, 'paid', start, end)
        buckets = list(db.scalars(select(QuotaPeriod).where(QuotaPeriod.owner_id == owner,
            QuotaPeriod.source == 'subscription').order_by(QuotaPeriod.starts_at)))
        assert len(buckets) == count
        assert all(bucket.granted == pages for bucket in buckets)
        assert [bucket.starts_at.day for bucket in buckets[:3]] == [31, 28, 31][:count]
        assert buckets[-1].ends_at == end
        assert first_subscription(db, owner, start)[0].id == buckets[0].id
        buckets[0].used = pages
        db.flush()
        assert first_subscription(db, owner, start) is None
        if count > 1:
            assert first_subscription(db, owner, datetime(2026, 2, 28))[0].id == buckets[1].id
        else:
            assert first_subscription(db, owner, end) is None


def test_models_are_restricted_to_the_confirmed_tiers(client):
    from app.translation_models import TranslationProvider
    from app.translation_providers import provider_resolver
    from manual_membership_server import seed_models
    with session_factory()() as db:
        db.execute(update(TranslationProvider).values(enabled=False))
        seed_models(db)
        for plan in ('guest', 'free', 'plus', 'pro'):
            resolve = provider_resolver(db, plan_id=plan)
            selected = {resolve(f'catalog-acceptance-{index}')['model'] for index in range(200)}
            expected = {'GPT 6 Luna'}
            if plan in ('plus', 'pro'):
                expected |= {'Gemini 3.8 Flash', 'DeepSeekv4 Pro', 'DeepSeek V4.1 Flash',
                             'Muse Spark 1.3', 'GPT-6.2 Sol', 'Gemini 4 Hydrogen'}
            assert selected == expected


@pytest.mark.parametrize('provider', ['stripe', 'creem'])
@pytest.mark.parametrize('plan,interval', [('plus', 'month'), ('pro', 'quarter')])
def test_new_checkout_never_requests_a_trial(client, monkeypatch, provider, plan, interval):
    from app import stripe_client, creem_client
    from app.config import settings
    from app.billing_models import BillingCheckout
    for key, value in {f'{provider.upper()}_ENABLED': 'true', f'{provider.upper()}_ENVIRONMENT': 'test',
            f'{provider.upper()}_RETURN_URL': 'https://comics.example/account/',
            'STRIPE_SECRET_KEY': 'sk_test_fixture', 'STRIPE_WEBHOOK_SECRET': 'whsec_fixture',
            'CREEM_API_KEY': 'creem_test_fixture', 'CREEM_WEBHOOK_SECRET': 'creem_fixture'}.items():
        monkeypatch.setenv(key, value)
    settings.cache_clear()
    price_id = f'{plan}-{interval}-prepaid-v1'
    with session_factory()() as db:
        db.get(BillingSettings, 1).default_provider = provider
        price = db.get(BillingPrice, price_id)
        price.status = 'active'
        amount = price.unit_amount
        db.add(BillingPriceBinding(id='checkout-binding', price_id=price_id, provider=provider,
            environment='test', product_id='prod_fixture', provider_price_id='price_fixture' if provider == 'stripe' else None,
            trial_product_id=None, status='active'))
        db.commit()
    dispatched = []
    def stripe_call(resource, action, *args, **kwargs):
        if resource == 'prices':
            return {'id': 'price_fixture', 'product': 'prod_fixture', 'livemode': False, 'active': True,
                'currency': 'usd', 'unit_amount': amount, 'type': 'one_time', 'recurring': None}
        assert (resource, action) == ('checkout.sessions', 'create')
        params = kwargs['params']
        assert params['mode'] == 'payment' and 'subscription_data' not in params
        dispatched.append(params)
        return {'id': 'cs_test_fixture', 'livemode': False, 'mode': 'payment', 'status': 'open',
            'metadata': params['metadata'], 'client_reference_id': params['client_reference_id'],
            'url': 'https://checkout.stripe.com/c/pay/cs_test_fixture'}
    def creem_call(method, path, **kwargs):
        if method == 'GET':
            assert path == '/products/prod_fixture'
            return {'id': 'prod_fixture', 'mode': 'test', 'status': 'active', 'currency': 'USD',
                'price': amount, 'billing_type': 'onetime', 'billing_period': 'once', 'trial_period_days': 0}
        params = kwargs['body']
        assert params['product_id'] == 'prod_fixture'
        dispatched.append(params)
        return {'id': 'ch_fixture', 'mode': 'test', 'status': 'pending', 'product': 'prod_fixture',
            'metadata': params['metadata'], 'request_id': params['request_id'],
            'checkout_url': 'https://creem.io/test/checkout/prod_fixture/ch_fixture'}
    monkeypatch.setattr(stripe_client, 'call', stripe_call)
    monkeypatch.setattr(creem_client, 'call', creem_call)
    auth = login(client, 'new-buyer')
    response = client.post('/v1/billing/checkouts', headers={**auth, 'Idempotency-Key': 'new-prepaid'},
        json={'price_id': price_id, 'provider': provider})
    assert response.status_code == 200, response.text
    assert len(dispatched) == 1
    with session_factory()() as db:
        assert db.scalar(select(BillingCheckout)).trial is False


@pytest.mark.parametrize('count', [1, 2, 4, 12])
def test_stripe_quarter_rejects_wrong_month_count(monkeypatch, count):
    from app.stripe_client import approved_price
    from app.billing_providers import BillingError
    from app.config import settings
    monkeypatch.setenv('STRIPE_ENVIRONMENT', 'test')
    settings.cache_clear()
    quote = SimpleNamespace(interval='quarter', billing_type='recurring', environment='test', stripe_price_id='price_fixture',
        stripe_product_id='prod_fixture', currency='usd', unit_amount=666)
    with pytest.raises(BillingError):
        approved_price({'livemode': False, 'id': 'price_fixture', 'product': 'prod_fixture',
            'currency': 'usd', 'unit_amount': 666, 'type': 'recurring',
            'recurring': {'interval': 'month', 'interval_count': count, 'usage_type': 'licensed'}}, quote)
