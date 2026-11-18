"""No R2/provider traffic: real API, persistent admission, ownership and races."""
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from threading import Barrier
from types import SimpleNamespace
from uuid import uuid4

import pytest
from sqlalchemy import func, select

from app.db import session_factory
from app.documents.models import Document, DocumentPage, DocumentQuotaAllocation
from app.documents.quota import reserve_document, settle_document
from app.entitlement_models import QuotaPeriod
from app.models import Job, Ledger, User, now, uid
from app.queue_models import JobStage
from conftest import configure_system_limits, login, login_plus
from test_quota_purchase_admission import seed_purchase
from test_translation_requests import high_control_budget
from translation_fixtures import configure_text_provider


class MetadataStore:
    def __init__(self):
        self.created, self.aborted, self.completed, self.signed = [], [], [], []

    def create_multipart(self, key, mime):
        self.created.append((key, mime))
        return 'isolated-upload-' + uid()

    def abort_multipart(self, key, upload_id):
        self.aborted.append((key, upload_id))

    def presign_upload_part(self, key, upload_id, part_number, *, byte_size=None):
        self.signed.append((key, upload_id, part_number, byte_size))
        return {'url': 'https://r2.example/isolated-part', 'headers': {}, 'expires_in': 300}

    def complete_multipart(self, key, upload_id, *, expected_size, expected_sha256=None):
        self.completed.append((key, upload_id))
        return {'key': key, 'byte_size': expected_size, 'sha256': expected_sha256}

    def presign_download(self, key, filename=None):
        return {'url': 'https://r2.example/isolated-result', 'headers': {}, 'expires_in': 300}


@pytest.fixture
def document_case(client, monkeypatch, request):
    from app.documents import api
    from app.translation_models import TranslationProvider
    auth = login(client)
    owner = client.get('/v1/me', headers=auth).json()['user']['id']
    configure_system_limits(free_daily_pages=0)
    high_control_budget()
    with session_factory()() as db:
        provider = db.scalar(select(TranslationProvider))
        configure_text_provider(db, provider.id, document={'enabled': True})
        provider_id = provider.id
    cfg = SimpleNamespace(enabled=True, r2_key_prefix='documents', upload_part_bytes=16 * 1024 * 1024,
                          upload_lifetime_seconds=3600, waiting_retention_hours=24)
    store = MetadataStore()
    monkeypatch.setattr(api, 'get_document_settings', lambda: cfg)
    monkeypatch.setattr(api, 'get_document_store', lambda: store)
    case = SimpleNamespace(client=client, auth=auth, owner=owner, provider=provider_id, store=store)
    if getattr(request, 'param', 'member') == 'member':
        paid_term(case, unlimited=False)  # Admission only; each test supplies its own page balance.
    return case


def purchase(case, pages, *, service='plus', ends_at=None):
    with session_factory()() as db:
        row = seed_purchase(db, case.owner, pages=pages, service=service,
                            start=now() - timedelta(days=1), end=ends_at)
        db.commit()
        return row.id


def body(case, **changes):
    return {'filename': 'book.zip', 'source_type': 'zip', 'byte_size': 1234,
            'sha256': 'a' * 64, 'target_language': 'zh-Hans', 'model_id': case.provider, **changes}


def create(case, key=None, **changes):
    key = key or str(uuid4())
    response = case.client.put('/v1/documents/' + key, headers=case.auth, json=body(case, **changes))
    assert response.status_code == 202, response.text
    return key


def prepare(case, key, pages, *, scope=None):
    """Trusted prepare fixture; not a claim that PDF parsing/OCR was executed."""
    with session_factory()() as db:
        job = db.scalar(select(Job).where(Job.owner_id == case.owner, Job.idempotency_key == key))
        document = db.get(Document, job.id)
        document.page_count = pages
        document.upload_completed_at = now()
        job.status, job.phase = 'preflight', 'doc_prepare'
        if scope is not None:
            job.config = {**job.config, 'document': {**job.config['document'], 'service_plans': scope}}
        for number in range(1, pages + 1):
            db.add(DocumentPage(job_id=job.id, page_index=number,
                input_ref={'key': f'documents/work/{job.id}/{number}.png', 'sha256': 'b' * 64,
                           'byte_size': 100, 'mime': 'image/png', 'width': 30, 'height': 40}))
        db.commit()
        return job.id


def reserve(job_id):
    with session_factory()() as db:
        accepted = reserve_document(db, db.get(Job, job_id))
        db.commit()
        return accepted


def paid_term(case, *, unlimited=True, kind='paid', service='plus', trial_pages=None):
    from app.billing_models import BillingCheckout, BillingPlan, BillingPlanRevision, BillingPrice, BillingPriceBinding, BillingSubscription, BillingTerm
    with session_factory()() as db:
        if not db.get(BillingPlan, service):
            db.add(BillingPlan(id=service, name=service))
            db.flush()
        version = (db.scalar(select(func.max(BillingPlanRevision.version)).where(BillingPlanRevision.plan_id == service)) or 0) + 1
        revision = BillingPlanRevision(plan_id=service, version=version, name=service,
            service_plan_id=service, monthly_classic_pages=None if unlimited else 0,
            trial_days=7, trial_classic_pages=trial_pages)
        db.add(revision)
        db.flush()
        price = BillingPrice(plan_id=service, plan_revision_id=revision.id, environment='test',
            currency='usd', unit_amount=999, interval='month', status='draft')
        db.add(price)
        db.flush()
        binding = BillingPriceBinding(price_id=price.id, provider='stripe', environment='test',
            product_id='prod_' + uid(), provider_price_id='price_' + uid(), status='draft')
        db.add(binding)
        db.flush()
        checkout = BillingCheckout(owner_id=case.owner, environment='test', provider='stripe', price_id=price.id,
            binding_id=binding.id, return_url='https://comics.example/account', trial=False,
            status='completed', expires_at=now() + timedelta(hours=1))
        db.add(checkout)
        db.flush()
        sub = BillingSubscription(id=uid(), owner_id=case.owner, checkout_id=checkout.id,
            environment='test', provider='stripe', customer_id='cus_' + uid(), price_id=price.id,
            binding_id=binding.id, status='active')
        db.add(sub)
        db.flush()
        term = BillingTerm(id=uid(), owner_id=case.owner, subscription_id=sub.id, price_id=price.id,
            kind=kind, starts_at=now() - timedelta(days=1), ends_at=now() + timedelta(days=29))
        db.add(term)
        if kind == 'trial' and trial_pages:
            db.flush()
            db.add(QuotaPeriod(id=uid(), owner_id=case.owner, billing_term_id=term.id,
                kind='classic_monthly', mode='classic', source='subscription', source_key='term:' + term.id,
                starts_at=term.starts_at, ends_at=term.ends_at, granted=trial_pages, used=0, reserved=0))
        db.commit()
        return term.id


@pytest.mark.parametrize('source', ['daily', 'grant', 'membership'])
def test_free_and_gifted_quota_can_fund_documents(document_case, source):
    case = document_case
    if source == 'daily':
        configure_system_limits(free_daily_pages=3)
    elif source == 'membership':
        login_plus(case.client, pages=3)
    else:
        with session_factory()() as db:
            db.add(QuotaPeriod(id=uid(), owner_id=case.owner, kind='classic_grant', mode='classic',
                source='grant', source_key='document-gift', starts_at=now() - timedelta(days=1),
                granted=3, used=0, reserved=0))
            db.commit()
    key = create(case)
    job_id = prepare(case, key, 3)
    assert reserve(job_id) and reserve(job_id)
    with session_factory()() as db:
        period = db.scalar(select(QuotaPeriod).where(QuotaPeriod.source == source))
        assert period.reserved == 3
        for page in db.scalars(select(DocumentPage)):
            page.status, page.billable = 'translated', True
        settle_document(db, db.get(Job, job_id))
        db.commit()
        assert (period.reserved, period.used) == (0, 3)
    assert case.client.get('/v1/documents/' + key, headers=case.auth).json()['quota']['used'] == 3


def test_capabilities_are_authenticated_and_document_model_specific(document_case):
    from app.translation_models import TranslationProvider
    case = document_case
    assert case.client.get('/v1/documents/capabilities').status_code == 401
    result = case.client.get('/v1/documents/capabilities', headers=case.auth)
    assert result.status_code == 200
    assert case.client.put('/v1/documents/' + str(uuid4()), json=body(case)).status_code == 401
    caps = result.json()
    assert caps['enabled'] and caps['unavailable_reason'] is None
    assert caps['limits']['max_bytes'] == 200_000_000 and caps['limits']['max_pages'] == 200
    assert [item['id'] for item in caps['models']] == [case.provider]
    assert not any(word in str(caps) for word in ('api_key', 'endpoint', 'revision_id'))
    with session_factory()() as db:
        provider = db.get(TranslationProvider, case.provider)
        configure_text_provider(db, provider.id, document={'enabled': False})
    assert case.client.get('/v1/documents/capabilities', headers=case.auth).json()['models'] == []


def test_document_membership_does_not_unlock_higher_tier_models(document_case):
    from app.translation_models import TranslationProvider
    case = document_case
    configure_system_limits(free_daily_pages=100)
    with session_factory()() as db:
        db.get(TranslationProvider, case.provider).text_plan_ids = ['pro']
        db.commit()
    assert case.client.get('/v1/documents/capabilities', headers=case.auth).json()['models'] == []
    response = case.client.put('/v1/documents/' + str(uuid4()), headers=case.auth, json=body(case))
    assert response.status_code == 403 and response.json()['error']['code'] == 'MODEL_DOCUMENT_UNSUPPORTED'
    assert not case.store.created


def test_model_catalogs_order_by_quota_multiplier(document_case):
    from app.translation_models import TranslationProvider
    case = document_case
    with session_factory()() as db:
        cheaper = configure_text_provider(db, document={'enabled': True})
        same_rate = configure_text_provider(db, document={'enabled': True})
        cheaper.quota_multiplier = same_rate.quota_multiplier = 0.25
        db.get(TranslationProvider, case.provider).quota_multiplier = 2.5
        db.commit()
    expected = [(cheaper.id, 0.25), (same_rate.id, 0.25), (case.provider, 2.5)]
    for path, field in [('/v1/capabilities', 'translation_models'), ('/v1/documents/capabilities', 'models')]:
        response = case.client.get(path, headers=case.auth)
        assert response.status_code == 200, response.text
        assert [(model['id'], model['quota_multiplier']) for model in response.json()[field]] == expected


@pytest.mark.parametrize('document_case', ['unsubscribed'], indirect=True)
def test_document_catalog_and_explicit_intent_read_pack_right_once_without_pricing_pages(document_case):
    from sqlalchemy import event
    from app.db import engine
    from app.translation_models import TranslationProvider
    case = document_case
    purchase(case, 1)
    with session_factory()() as db:
        db.get(TranslationProvider, case.provider).text_plan_ids = ['plus']
        for rate in (2, 3, 4):
            model = configure_text_provider(db, document={'enabled': True})
            model.text_plan_ids, model.quota_multiplier = ['plus'], rate
        db.commit()
    reads = []
    def record(_conn, _cursor, sql, *_):
        if sql.startswith('SELECT') and 'FROM quota_periods' in sql:
            reads.append(sql)
    event.listen(engine(), 'before_cursor_execute', record)
    try:
        caps = case.client.get('/v1/documents/capabilities', headers=case.auth).json()
        assert len(caps['models']) == 4
        create(case)
    finally:
        event.remove(engine(), 'before_cursor_execute', record)
    assert len(reads) == 2 and all('JOIN billing_orders' in sql and 'LIMIT' in sql for sql in reads)


@pytest.mark.parametrize('document_case', ['unsubscribed'], indirect=True)
def test_upload_admission_is_independent_of_scheduler_and_unrelated_owner(document_case):
    from app.entitlements import locked_user
    from app.scheduler import lock_scheduler
    from test_cluster_submissions import descriptor, submit
    case = document_case
    other = login_plus(case.client, 'locked-owner')
    page = descriptor(b'independent')
    replay = submit(case.client, other, page)
    assert replay.status_code == 202
    owner = case.client.get('/v1/me', headers=other).json()['user']['id']
    period = purchase(case, 2)
    with session_factory()() as held:
        lock_scheduler(held)
        locked_user(held, owner)
        # Both requests must commit on other connections while these locks are still held.
        assert submit(case.client, case.auth, page).status_code == 202
        create(case)
        assert submit(case.client, other, page).json()['id'] == replay.json()['id']
    with session_factory()() as db:
        assert db.get(QuotaPeriod, period).reserved == 1


@pytest.mark.parametrize('document_case', ['unsubscribed'], indirect=True)
def test_book_auto_routes_around_unpayable_model_while_explicit_choice_waits_for_quota(document_case):
    from app.translation_models import TranslationProvider
    from app.providers import digest
    from app.translation_routing import choose_provider
    case = document_case
    bucket = purchase(case, 1)
    with session_factory()() as db:
        costly = db.get(TranslationProvider, case.provider)
        costly.text_plan_ids, costly.quota_multiplier = ['plus'], 2
        cheap = configure_text_provider(db, document={'enabled': True})
        cheap.text_plan_ids = ['plus']
        db.commit()
    weights = {costly.id: 1, cheap.id: 1}
    hashes = (f'{index:064x}' for index in range(100))
    sha = next(value for value in hashes if choose_provider(weights, digest([value, 'zh-Hans'])) == costly.id)
    key = create(case, sha256=sha)
    assert not reserve(prepare(case, key, 1))
    auto = create(case, sha256=sha, model_id=None)
    assert reserve(prepare(case, auto, 1))
    result = case.client.get('/v1/documents/' + auto, headers=case.auth).json()
    assert result['model']['id'] == cheap.id and result['quota']['reserved'] == 1
    with session_factory()() as db:
        assert db.get(QuotaPeriod, bucket).reserved == 1


@pytest.mark.parametrize('document_case', ['unsubscribed'], indirect=True)
@pytest.mark.parametrize('access', ['gift_pages', 'pending_gift', 'expired_gift', 'future_term', 'revoked_trial',
                                   'expired_pack', 'future_pack', 'revoked_pack', 'spent_pack', 'reserved_pack'])
def test_document_creation_needs_live_paid_access(document_case, access):
    from app.billing_models import BillingTerm
    case = document_case
    configure_system_limits(free_daily_pages=20)
    if access.endswith('_pack'):
        period_id = purchase(case, 5)
        with session_factory()() as db:
            period = db.get(QuotaPeriod, period_id)
            if access == 'expired_pack': period.ends_at = now()
            elif access == 'future_pack': period.starts_at = now() + timedelta(days=1)
            elif access == 'revoked_pack': period.revoked_at = now()
            elif access == 'spent_pack': period.used = 5
            else: period.reserved = 5
            db.commit()
    elif access == 'gift_pages':
        with session_factory()() as db:
            db.add(QuotaPeriod(id=uid(), owner_id=case.owner, kind='classic_grant', mode='classic',
                source='grant', source_key='gift', starts_at=now(), granted=100))
            db.commit()
    elif access in ('pending_gift', 'expired_gift'):
        login_plus(case.client)
        with session_factory()() as db:
            user = db.get(User, case.owner)
            if access == 'pending_gift':
                user.plus_pending = True
            else:
                user.plus_expires_at = now()
            db.commit()
    elif access in ('future_term', 'revoked_trial'):
        term_id = paid_term(case, kind='trial')
        with session_factory()() as db:
            term = db.get(BillingTerm, term_id)
            if access == 'future_term':
                term.starts_at = now() + timedelta(days=1)
            else:
                term.revoked_at = now()
            db.commit()
    caps = case.client.get('/v1/documents/capabilities', headers=case.auth).json()
    assert not caps['enabled'] and caps['models'] == [] and caps['unavailable_reason'] == 'membership_required'
    for filename, kind in [('book.zip', 'zip'), ('book.cbz', 'zip'), ('book.pdf', 'pdf')]:
        result = case.client.put('/v1/documents/' + str(uuid4()), headers=case.auth,
            json=body(case, filename=filename, source_type=kind))
        assert result.status_code == 403 and result.json()['error']['code'] == 'DOCUMENT_MEMBERSHIP_REQUIRED'
    assert not case.store.created and not case.store.signed
    with session_factory()() as db:
        assert not db.scalar(select(Job.id)) and not db.scalar(select(Ledger.id))


@pytest.mark.parametrize('document_case', ['unsubscribed'], indirect=True)
@pytest.mark.parametrize('membership', ['paid', 'trial', 'gift'])
def test_whole_file_freezes_membership_before_upload_and_replays_after_expiry(document_case, membership):
    from app.billing_models import BillingTerm
    case = document_case
    if membership == 'gift':
        login_plus(case.client, pages=0)
    else:
        term_id = paid_term(case, kind=membership, unlimited=False, trial_pages=0)
    key = create(case)
    with session_factory()() as db:
        job = db.scalar(select(Job).where(Job.idempotency_key == key))
        assert job.entitlement['priority'] == 1
        if membership == 'gift':
            db.get(User, case.owner).plus_expires_at = now()
        else:
            db.get(BillingTerm, term_id).ends_at = now()
        db.commit()
    assert create(case, key) == key and len(case.store.created) == 1
    assert case.client.get('/v1/documents', headers=case.auth).json()['items'][0]['id'] == key
    assert case.client.post('/v1/documents/' + key + '/complete-upload', headers=case.auth).status_code == 202
    with session_factory()() as db:
        assert db.scalar(select(JobStage.service_priority).where(JobStage.job_id == job.id)) == 1
    assert case.client.put('/v1/documents/' + str(uuid4()), headers=case.auth, json=body(case)).status_code == 403


@pytest.mark.parametrize('trial_pages', [0, 2])
@pytest.mark.parametrize('document_case', ['unsubscribed'], indirect=True)
def test_finite_trial_is_not_mistaken_for_its_unlimited_paid_plan(document_case, trial_pages):
    case = document_case
    paid_term(case, kind='trial', trial_pages=trial_pages)
    job_id = prepare(case, create(case), 3, scope=['plus'])
    assert not reserve(job_id)
    with session_factory()() as db:
        assert db.get(Job, job_id).entitlement['document_quota']['available'] == trial_pages
        assert db.scalar(select(func.count()).select_from(DocumentQuotaAllocation)) == 0
    if trial_pages:
        funded = prepare(case, create(case), trial_pages, scope=['plus'])
        assert reserve(funded)
        with session_factory()() as db:
            assert db.scalar(select(QuotaPeriod.reserved)) == trial_pages


@pytest.mark.parametrize('kind', ['paid', 'trial'])
def test_unlimited_term_tracks_all_200_processing_pages_without_finite_bucket(document_case, kind):
    case = document_case
    term_id = paid_term(case, kind=kind)
    job_id = prepare(case, create(case), 200, scope=['plus'])
    assert reserve(job_id)
    with session_factory()() as db:
        allocation = db.scalar(select(DocumentQuotaAllocation))
        assert (allocation.period_id, allocation.billing_term_id, allocation.pages, allocation.reserved) == (None, term_id, 200, 200)
        assert db.get(Job, job_id).quota_pages == 200
        assert db.scalar(select(func.count()).select_from(QuotaPeriod)) == 0
        for page in db.scalars(select(DocumentPage)):
            page.status, page.billable = 'translated', True
        settle_document(db, db.get(Job, job_id))
        db.commit()
        assert (allocation.reserved, allocation.used, allocation.released) == (0, 200, 0)
        assert db.scalar(select(func.sum(Ledger.amount)).where(Ledger.kind == 'settle')) == 200


@pytest.mark.parametrize('success', [True, False])
@pytest.mark.parametrize('daily_pages', [0, 1])
def test_unlimited_gift_reserves_and_settles_without_a_finite_bucket(document_case, success, daily_pages):
    case = document_case
    login_plus(case.client)
    configure_system_limits(free_daily_pages=daily_pages)
    key = create(case)
    job_id = prepare(case, key, 3)
    assert reserve(job_id) and reserve(job_id)
    assert case.client.get('/v1/documents/' + key, headers=case.auth).json()['quota']['reserved'] == 3
    with session_factory()() as db:
        job = db.get(Job, job_id)
        gift = job.entitlement['document_unlimited_gift']
        assert gift['membership_id'] == db.get(User, case.owner).membership_id
        assert gift['reserved'] == 3 - daily_pages
        assert db.scalar(select(func.count()).select_from(QuotaPeriod)) == daily_pages
        pages = list(db.scalars(select(DocumentPage).order_by(DocumentPage.page_index)))
        for page in pages:
            page.status, page.billable = 'translated', True
        pages[-1].status, pages[-1].billable = 'no_text', False
        db.get(User, case.owner).plus_expires_at = now() - timedelta(seconds=1)
        settle_document(db, job, success=success)
        db.commit()
        settle_document(db, job, success=success)
        db.commit()
        assert db.scalar(select(func.sum(Ledger.amount)).where(Ledger.kind == 'reserve')) == 3
        assert db.scalar(select(func.sum(Ledger.amount)).where(Ledger.kind == 'release')) == (1 if success else 3)
    quota = case.client.get('/v1/documents/' + key, headers=case.auth).json()['quota']
    assert (quota['reserved'], quota['used'], quota['released']) == ((0, 2, 1) if success else (0, 0, 3))
    history = case.client.get('/v1/documents', headers=case.auth).json()
    assert history['items'][0]['quota'] == quota
    assert case.client.post('/v1/documents/' + key + '/cancel', headers=case.auth).status_code == 409


@pytest.mark.parametrize('restriction', ['expired', 'pending', 'scope'])
def test_unlimited_gift_still_requires_active_and_model_eligible_membership(document_case, restriction):
    case = document_case
    login_plus(case.client)
    job_id = prepare(case, create(case), 2, scope=['pro'] if restriction == 'scope' else ['plus'])
    with session_factory()() as db:
        user = db.get(User, case.owner)
        if restriction == 'expired': user.plus_expires_at = now() - timedelta(seconds=1)
        if restriction == 'pending': user.plus_pending = True
        db.commit()
    assert not reserve(job_id)
    with session_factory()() as db:
        assert 'document_unlimited_gift' not in db.get(Job, job_id).entitlement
        assert db.scalar(select(func.count()).select_from(Ledger).where(Ledger.kind == 'reserve')) == 0


def test_unlimited_lower_plan_does_not_pay_higher_plan_model(document_case):
    case = document_case
    paid_term(case, service='plus')
    job_id = prepare(case, create(case), 3, scope=['pro'])
    assert not reserve(job_id)
    with session_factory()() as db:
        assert db.scalar(select(func.count()).select_from(DocumentQuotaAllocation)) == 0
        assert db.get(Job, job_id).entitlement['document_quota']['difference'] == 3


def test_upload_request_replay_ownership_and_strict_metadata(document_case):
    case = document_case
    purchase(case, 5)
    key = create(case)
    assert create(case, key) == key and len(case.store.created) == 1
    different = case.client.put('/v1/documents/' + key, headers=case.auth, json=body(case, byte_size=1235))
    assert different.status_code == 409
    other = login(case.client, 'other')
    for suffix in ('', '/download'):
        assert case.client.get('/v1/documents/' + key + suffix, headers=other).status_code == 404
    for suffix in ('/cancel', '/resume', '/complete-upload'):
        assert case.client.post('/v1/documents/' + key + suffix, headers=other).status_code == 404
    invalid = case.client.put('/v1/documents/' + str(uuid4()), headers=case.auth,
                              json=body(case, byte_size=200000001))
    assert invalid.status_code == 422
    parts = case.client.post('/v1/documents/' + key + '/upload-parts', headers=case.auth,
                             json={'part_numbers': [1]})
    assert parts.status_code == 200 and case.store.signed[0][3] == 1234
    assert case.client.post('/v1/documents/' + key + '/upload-parts', headers=case.auth,
                            json={'part_numbers': [2]}).status_code == 422


def test_cancellation_before_reservation_is_durable_and_idempotent(document_case):
    case = document_case
    purchase(case, 5)
    key = create(case)
    for _ in range(2):
        result = case.client.post('/v1/documents/' + key + '/cancel', headers=case.auth)
        assert result.status_code == 200 and result.json()['status'] == 'cancelled'
    assert create(case, key) == key and len(case.store.created) == 1
    assert case.client.post('/v1/documents/' + key + '/complete-upload', headers=case.auth).json()['status'] == 'cancelled'
    assert case.client.post('/v1/documents/' + key + '/resume', headers=case.auth).status_code == 409
    with session_factory()() as db:
        assert db.scalar(select(func.count()).select_from(Ledger).where(Ledger.kind == 'reserve')) == 0


def test_whole_document_no_partial_reservation_and_explicit_resume(document_case):
    case = document_case
    first = purchase(case, 2)
    key = create(case)
    job_id = prepare(case, key, 3)
    assert not reserve(job_id)
    response = case.client.get('/v1/documents/' + key, headers=case.auth)
    assert {field: response.json()['quota'][field] for field in ('needed', 'available', 'difference')} == {
        'needed': 3, 'available': 2, 'difference': 1}
    with session_factory()() as db:
        assert db.get(QuotaPeriod, first).reserved == 0
        assert db.scalar(select(func.count()).select_from(DocumentQuotaAllocation)) == 0
        assert db.get(Job, job_id).status == 'awaiting_quota'
    purchase(case, 2)
    # Payment alone does not queue or reserve the waiting job.
    assert case.client.get('/v1/documents/' + key, headers=case.auth).json()['status'] == 'awaiting_quota'
    response = case.client.post('/v1/documents/' + key + '/resume', headers=case.auth)
    assert response.status_code == 202 and response.json()['quota']['reserved'] == 3
    assert case.client.post('/v1/documents/' + key + '/resume', headers=case.auth).json()['quota']['reserved'] == 3
    denied = case.client.post('/v1/documents/' + key + '/cancel', headers=case.auth)
    assert denied.status_code == 409 and denied.json()['error']['code'] == 'DOCUMENT_NOT_CANCELLABLE'


def test_model_scope_cannot_borrow_rights_from_another_bucket(document_case):
    case = document_case
    pro = purchase(case, 1, service='pro')
    plus = purchase(case, 10, service='plus')
    job_id = prepare(case, create(case), 3, scope=['pro'])
    assert not reserve(job_id)
    with session_factory()() as db:
        assert db.get(QuotaPeriod, pro).reserved == db.get(QuotaPeriod, plus).reserved == 0
    purchase(case, 2, service='pro')
    assert reserve(job_id)
    with session_factory()() as db:
        allocations = list(db.scalars(select(DocumentQuotaAllocation)))
        assert sum(row.reserved for row in allocations) == 3
        assert all(row.entitlement['service_plan'] == 'pro' for row in allocations)
        assert db.get(QuotaPeriod, plus).reserved == 0


@pytest.mark.parametrize('document_case', ['unsubscribed'], indirect=True)
def test_free_model_preserves_free_then_paid_order(document_case):
    case = document_case
    configure_system_limits(free_daily_pages=1)
    purchased = purchase(case, 5)
    job_id = prepare(case, create(case), 3)
    assert reserve(job_id)
    with session_factory()() as db:
        job = db.get(Job, job_id)
        assert job.entitlement['plan'] == 'free' and job.service_priority == 1
        assert db.get(QuotaPeriod, purchased).reserved == 2
        assert db.scalar(select(QuotaPeriod.reserved).where(QuotaPeriod.owner_id == case.owner,
                                                          QuotaPeriod.source == 'daily')) == 1


@pytest.mark.parametrize('success', [True, False])
def test_multi_bucket_settlement_exactly_once_original_buckets_and_no_text(document_case, success):
    case = document_case
    early = purchase(case, 2, ends_at=now() + timedelta(hours=1))
    late = purchase(case, 2)
    key = create(case)
    job_id = prepare(case, key, 3)
    assert reserve(job_id) and reserve(job_id)
    with session_factory()() as db:
        pages = list(db.scalars(select(DocumentPage).order_by(DocumentPage.page_index)))
        for page in pages:
            page.status, page.billable = 'translated', True
        pages[1].status, pages[1].billable = 'no_text', False
        db.get(QuotaPeriod, early).ends_at = now() - timedelta(seconds=1)
        db.get(QuotaPeriod, late).revoked_at = now()
        settle_document(db, db.get(Job, job_id), success=success)
        db.commit()
        settle_document(db, db.get(Job, job_id), success=success)
        db.commit()
        assert db.get(QuotaPeriod, early).reserved == db.get(QuotaPeriod, late).reserved == 0
        assert (db.get(QuotaPeriod, early).used, db.get(QuotaPeriod, late).used) == ((1, 1) if success else (0, 0))
        assert sum(row.amount for row in db.scalars(select(Ledger).where(Ledger.kind == 'reserve'))) == 3
    # Even a released/settled task retains its irreversible admission boundary.
    assert case.client.post('/v1/documents/' + key + '/cancel', headers=case.auth).status_code == 409


@pytest.mark.parametrize('document_case', ['unsubscribed'], indirect=True)
def test_parallel_documents_compete_for_total_balance_not_individual_buckets(document_case):
    case = document_case
    period = purchase(case, 3)
    jobs = [prepare(case, create(case), 3) for _ in range(2)]
    barrier = Barrier(2)
    def attempt(job_id):
        barrier.wait(timeout=10)
        return reserve(job_id)
    with ThreadPoolExecutor(2) as pool:
        assert sorted(pool.map(attempt, jobs)) == [False, True]
    with session_factory()() as db:
        assert db.get(QuotaPeriod, period).reserved == 3
        assert db.scalar(select(func.count()).select_from(DocumentQuotaAllocation)) == 1


@pytest.mark.parametrize('document_case', ['unsubscribed'], indirect=True)
def test_book_and_image_cannot_both_reserve_the_last_pack_page(document_case):
    from test_cluster_submissions import descriptor, submit
    case = document_case
    period = purchase(case, 1)
    book = prepare(case, create(case), 1)
    barrier = Barrier(2)
    def image():
        barrier.wait(timeout=10)
        return submit(case.client, case.auth, descriptor(b'last'), model_id=case.provider)
    with ThreadPoolExecutor(1) as pool:
        submitting = pool.submit(image)
        barrier.wait(timeout=10)
        book_won = reserve(book)
        assert submitting.result().status_code == (403 if book_won else 202)
    with session_factory()() as db:
        assert db.get(QuotaPeriod, period).reserved == 1
        assert db.scalars(select(Ledger.amount).where(Ledger.kind == 'reserve')).all() == [1]


def test_cancel_races_reservation_with_one_serialized_winner(document_case):
    case = document_case
    purchase(case, 3)
    key = create(case)
    job_id = prepare(case, key, 3)
    barrier = Barrier(2)
    def accept():
        from fastapi import HTTPException
        barrier.wait(timeout=10)
        try:
            return reserve(job_id)
        except HTTPException as error:
            assert error.detail['code'] == 'DOCUMENT_NOT_RESUMABLE'
            return False
    def cancel():
        barrier.wait(timeout=10)
        return case.client.post('/v1/documents/' + key + '/cancel', headers=case.auth).status_code
    with ThreadPoolExecutor(2) as pool:
        accepted, cancelled = pool.submit(accept), pool.submit(cancel)
        won, code = accepted.result(), cancelled.result()
    assert (won, code) in ((True, 409), (False, 200))
    with session_factory()() as db:
        document = db.get(Document, job_id)
        assert bool(document.reserved_at) is won
        assert db.scalar(select(func.coalesce(func.sum(QuotaPeriod.reserved), 0))) == (3 if won else 0)


def test_multi_bucket_reservation_rolls_back_as_one_transaction(document_case, monkeypatch):
    from app.documents import quota
    case = document_case
    purchase(case, 2)
    purchase(case, 2)
    job_id = prepare(case, create(case), 3)
    original, calls = quota.reserve_period, []
    def fail_second(*args):
        calls.append(1)
        if len(calls) == 2:
            raise RuntimeError('isolated second bucket failure')
        return original(*args)
    monkeypatch.setattr(quota, 'reserve_period', fail_second)
    with pytest.raises(RuntimeError, match='second bucket'):
        reserve(job_id)
    with session_factory()() as db:
        assert db.scalar(select(func.sum(QuotaPeriod.reserved))) == 0
        assert db.scalar(select(func.count()).select_from(DocumentQuotaAllocation)) == 0
        assert db.scalar(select(func.count()).select_from(Ledger).where(Ledger.kind == 'reserve')) == 0
        assert db.get(Document, job_id).reserved_at is None


def test_seal_replay_and_metadata_only_history(document_case):
    case = document_case
    purchase(case, 5)
    key = create(case)
    for _ in range(2):
        result = case.client.post('/v1/documents/' + key + '/complete-upload', headers=case.auth)
        assert result.status_code == 202 and result.json()['status'] == 'preflight'
    assert len(case.store.completed) == 1
    with session_factory()() as db:
        document = db.scalar(select(Document))
        assert document.expires_at > now() + timedelta(hours=23)
        assert db.scalar(select(func.count()).select_from(JobStage).where(JobStage.name == 'doc_prepare')) == 1
    history = case.client.get('/v1/documents', headers=case.auth)
    assert history.status_code == 200 and history.json()['items'][0]['id'] == key
    assert 'https://r2.example' not in history.text and 'source_key' not in history.text


@pytest.mark.parametrize('document_case', ['unsubscribed'], indirect=True)
def test_purchase_exhaustion_blocks_new_documents_but_not_replay_or_resume(document_case):
    case = document_case
    period_id = purchase(case, 1)
    key = create(case)
    job_id = prepare(case, key, 2)
    with session_factory()() as db:
        db.get(QuotaPeriod, period_id).used = 1
        db.commit()
    assert not case.client.get('/v1/documents/capabilities', headers=case.auth).json()['enabled']
    assert case.client.put('/v1/documents/' + str(uuid4()), headers=case.auth, json=body(case)).status_code == 403
    assert create(case, key) == key and len(case.store.created) == 1
    assert reserve(job_id) is False
    snapshot = case.client.get('/v1/documents/' + key, headers=case.auth).json()
    assert snapshot['status'] == 'awaiting_quota' and snapshot['quota']['available'] == 0
    purchase(case, 2)
    assert case.client.get('/v1/documents/capabilities', headers=case.auth).json()['enabled']
    assert case.client.get('/v1/documents/' + key, headers=case.auth).json()['status'] == 'awaiting_quota'
    response = case.client.post('/v1/documents/' + key + '/resume', headers=case.auth)
    assert response.status_code == 202 and response.json()['quota']['reserved'] == 2


def test_history_cursor_and_snapshot_queries_are_bounded_not_per_document(document_case):
    from sqlalchemy import event
    from app.db import engine
    case = document_case
    purchase(case, 1)
    keys = [create(case) for _ in range(22)]
    statements = []
    def observed(_connection, _cursor, statement, _parameters, _context, _executemany):
        if 'document_pages' in statement or 'document_quota_allocations' in statement:
            statements.append(statement)
    event.listen(engine(), 'before_cursor_execute', observed)
    try:
        first = case.client.get('/v1/documents?limit=20', headers=case.auth).json()
    finally:
        event.remove(engine(), 'before_cursor_execute', observed)
    assert len(first['items']) == 20 and len(statements) == 2
    second = case.client.get('/v1/documents?limit=20&before=' + first['next_cursor'], headers=case.auth).json()
    assert len(second['items']) == 2 and second['next_cursor'] is None
    assert {item['id'] for item in first['items'] + second['items']} == set(keys)
    other = login(case.client, 'history-other-owner')
    assert case.client.get('/v1/documents?before=' + first['next_cursor'], headers=other).status_code == 404
    assert case.client.get('/v1/documents?limit=51', headers=case.auth).status_code == 422
