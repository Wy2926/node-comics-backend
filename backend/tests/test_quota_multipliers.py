"""Decimal admission, immutable pricing and settlement without upstream traffic."""
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from decimal import Decimal
from threading import Barrier

import pytest
from sqlalchemy import func, select

from app.db import session_factory
from app.documents.models import DocumentPage, DocumentQuotaAllocation
from app.documents.quota import settle_document
from app.entitlement_models import QuotaPeriod
from app.entitlements import settle
from app.models import Job, Ledger, now
from app.translation_models import TranslationProvider
from conftest import configure_system_limits, login, login_plus
from test_cluster_submissions import descriptor, submit
from test_document_quota_api import document_case, create as create_document, prepare, purchase, reserve  # noqa: F401
from test_quota_purchase_admission import seed_purchase
from test_translation_model_selection import job
from test_translation_providers import PATH, admin_case, body, create  # noqa: F401
from test_translation_requests import high_control_budget


def priced(case, rate, plans=None):
    return create(case, {**body(), 'quota_multiplier': rate, 'text_plan_ids': plans})


def change_rate(case, provider, rate):
    response = case[0].put(PATH + '/' + provider['id'], headers=case[1],
                          json={**body(), 'quota_multiplier': rate, 'text_plan_ids': provider['text_plan_ids']})
    assert response.status_code == 200, response.text
    return response.json()


def test_multiplier_validation_and_independence(admin_case):
    client, auth = admin_case
    from app.translation_providers import ProviderWrite
    schema = ProviderWrite.model_json_schema()['properties']['quota_multiplier']
    assert schema['type'] == 'number' and schema['multipleOf'] == .0001
    assert schema['maximum'] == 10000 and schema['exclusiveMinimum'] == 0
    for value in (0, -1, True, '0.5', 'NaN', 'Infinity', 0.00001, 10001, None):
        response = client.post(PATH, headers=auth, json={**body(), 'quota_multiplier': value})
        assert response.status_code == 422, response.text
    provider = priced(admin_case, 0.125)
    updated = change_rate(admin_case, provider, 2.5)
    assert updated['quota_multiplier'] == 2.5
    assert updated['revision_id'] == provider['revision_id']
    assert updated['config'] == provider['config'] and 'quota_multiplier' not in updated['config']
    omitted = client.put(PATH + '/' + provider['id'], headers=auth, json=body())
    assert omitted.json()['quota_multiplier'] == 2.5
    public = client.get('/v1/capabilities').json()['translation_models'][0]
    assert public['quota_multiplier'] == 2.5 and not public['requires_paid']


def test_price_changes_preserve_reuse_replay_and_original_reservation(admin_case):
    client, _ = admin_case
    provider = priced(admin_case, 0.5)
    auth = login(client)
    first = submit(client, auth, descriptor(b'original'), key='original', model_id=provider['id'])
    original = job(client, auth, first)
    assert first.json()['quota_multiplier'] == first.json()['quota_pages'] == 0.5
    change_rate(admin_case, provider, 2.5)
    for key in ('original', 'reuse'):
        response = submit(client, auth, descriptor(b'original'), key=key, model_id=provider['id'])
        assert job(client, auth, response).id == original.id
        assert response.json()['quota_multiplier'] == 0.5
    new = job(client, auth, submit(client, auth, descriptor(b'new'), key='new', model_id=provider['id']))
    assert new.quota_pages == Decimal('2.5')
    assert new.config == original.config
    with session_factory()() as db:
        entry = db.get(Job, original.id)
        entry.status = 'cancelled'
        settle(db, entry, success=False)
        settle(db, entry, success=True)
        db.commit()
        assert db.get(QuotaPeriod, original.quota_period_id).reserved == Decimal('2.5')
        assert db.scalar(select(Ledger.amount).where(Ledger.job_id == original.id, Ledger.kind == 'release')) == Decimal('.5')


def test_decimal_tenths_exhaust_exactly_and_summary_keeps_fractions(admin_case):
    client, _ = admin_case
    provider = priced(admin_case, 0.1)
    configure_system_limits(free_daily_pages=1)
    high_control_budget()
    auth = login(client)
    jobs = [job(client, auth, submit(client, auth, descriptor(str(index).encode()), key=str(index),
                                   model_id=provider['id'])) for index in range(10)]
    assert submit(client, auth, descriptor(b'exhausted'), key='exhausted', model_id=provider['id']).status_code == 403
    assert not client.get('/v1/capabilities', headers=auth).json()['translation_models'][0]['available']
    with session_factory()() as db:
        for index, accepted in enumerate(jobs):
            entry = db.get(Job, accepted.id)
            entry.status = 'succeeded' if index < 3 else 'failed'
            settle(db, entry, success=index < 3)
            settle(db, entry, success=index < 3)
        db.commit()
        bucket = db.get(QuotaPeriod, jobs[0].quota_period_id)
        assert (bucket.used, bucket.reserved) == (Decimal('.3'), 0)
    usage = client.get('/v1/me/usage', headers=auth)
    assert usage.status_code == 200, usage.text
    assert usage.json()['entitlements']['free_quota']['available'] == 0.7
    summary = client.get('/v1/me/usage/summary', headers=auth)
    assert summary.status_code == 200, summary.text
    assert summary.json()['quota_used']['classic'] == 0.3


def test_rates_do_not_change_free_paid_scope_or_unlimited_membership(admin_case):
    client, _ = admin_case
    free, paid = priced(admin_case, 2.5), priced(admin_case, .25, ['plus'])
    auth = login_plus(client, pages=1)
    first = job(client, auth, submit(client, auth, descriptor(b'free'), key='free', model_id=free['id']))
    second = job(client, auth, submit(client, auth, descriptor(b'paid'), key='paid', model_id=paid['id']))
    assert (first.quota_kind, first.quota_pages) == ('classic_daily', Decimal('2.5'))
    assert (second.quota_kind, second.quota_pages) == ('classic_monthly', Decimal('.25'))
    outsider = login(client, 'outsider')
    assert submit(client, outsider, descriptor(b'locked'), model_id=paid['id']).status_code == 403
    unlimited = login_plus(client, 'unlimited')
    included = job(client, unlimited, submit(client, unlimited, descriptor(b'included'), model_id=paid['id']))
    assert included.settlement == 'included' and included.quota_pages == 0
    assert included.quota_multiplier == Decimal('.25')


def test_admission_skips_small_bucket_and_preserves_scope(admin_case):
    client, _ = admin_case
    auth = login(client)
    owner = client.get('/v1/me', headers=auth).json()['user']['id']
    with session_factory()() as db:
        small = seed_purchase(db, owner, pages=1, service='plus', end=now() + timedelta(hours=1))
        enough = seed_purchase(db, owner, pages=3, service='plus')
        small_id, enough_id = small.id, enough.id
        db.commit()
    provider = priced(admin_case, 1.5, ['plus'])
    accepted = job(client, auth, submit(client, auth, descriptor(b'large'), model_id=provider['id']))
    assert accepted.quota_period_id == enough_id
    with session_factory()() as db:
        assert db.get(QuotaPeriod, small_id).reserved == 0
    change_rate(admin_case, provider, 4)
    choice = next(item for item in client.get('/v1/capabilities', headers=auth).json()['translation_models'] if item['id'] == provider['id'])
    assert not choice['available'] and choice['unavailable_reason'] == 'quota_exhausted'


def test_fractional_last_balance_is_atomic(admin_case):
    client, _ = admin_case
    provider = priced(admin_case, .75)
    auth = login(client)
    configure_system_limits(free_daily_pages=1)
    high_control_budget()
    barrier = Barrier(2)
    def send(index):
        barrier.wait(timeout=10)
        return submit(client, auth, descriptor(str(index).encode()), key=str(index), model_id=provider['id'])
    with ThreadPoolExecutor(2) as pool:
        results = list(pool.map(send, range(2)))
    assert sorted(response.status_code for response in results) == [202, 403]
    with session_factory()() as db:
        assert db.scalar(select(QuotaPeriod.reserved)) == Decimal('.75')
        assert db.scalar(select(func.count()).select_from(Ledger).where(Ledger.kind == 'reserve')) == 1


def test_fractional_grant_and_compensation_are_idempotent(admin_case):
    client, admin = admin_case
    auth = login(client)
    owner = client.get('/v1/me', headers=auth).json()['user']['id']
    for endpoint, extra in [('quota-grants', {'mode': 'classic'}), ('quota-compensations', {'kind': 'classic_daily'})]:
        values = {**extra, 'pages': .0001, 'note': 'fractional quota'}
        headers = {**admin, 'Idempotency-Key': endpoint}
        first = client.post(f'/v1/admin/users/{owner}/{endpoint}', headers=headers, json=values)
        assert first.status_code in (200, 201), first.text
        assert client.post(f'/v1/admin/users/{owner}/{endpoint}', headers=headers, json=values).json() == first.json()
    with session_factory()() as db:
        assert db.scalar(select(func.sum(Ledger.amount)).where(Ledger.owner_id == owner)) == Decimal('.0002')


@pytest.mark.parametrize('rate', [Decimal('2.5'), Decimal('0.1')])
def test_document_freezes_rate_and_settles_billable_pages_only(document_case, rate):
    case = document_case
    with session_factory()() as db:
        db.get(TranslationProvider, case.provider).quota_multiplier = rate
        db.commit()
    bucket_id = purchase(case, 10)
    key = create_document(case)
    with session_factory()() as db:
        db.get(TranslationProvider, case.provider).quota_multiplier = Decimal('9')
        db.commit()
    job_id = prepare(case, key, 3)
    assert reserve(job_id) and reserve(job_id)
    snapshot = case.client.get('/v1/documents/' + key, headers=case.auth).json()
    assert snapshot['quota_multiplier'] == float(rate)
    assert snapshot['quota']['needed'] == snapshot['quota']['reserved'] == float(rate * 3)
    with session_factory()() as db:
        pages = list(db.scalars(select(DocumentPage).where(DocumentPage.job_id == job_id).order_by(DocumentPage.page_index)))
        for page in pages:
            page.status, page.billable = 'translated', True
        pages[1].billable = False
        pages[2].quality_flags = ['unrecognized_regions']
        entry = db.get(Job, job_id)
        settle_document(db, entry)
        db.commit()
        settle_document(db, entry)
        db.commit()
        bucket = db.get(QuotaPeriod, bucket_id)
        assert (bucket.reserved, bucket.used) == (0, rate)
        allocation = db.scalar(select(DocumentQuotaAllocation).where(DocumentQuotaAllocation.job_id == job_id))
        assert (allocation.pages, allocation.used, allocation.released) == (3, rate, rate * 2)
    snapshot = case.client.get('/v1/documents/' + key, headers=case.auth).json()
    assert snapshot['quota']['used'] == float(rate) and snapshot['quota']['released'] == float(rate * 2)


def test_fractional_document_buckets_reserve_all_or_none(document_case):
    case = document_case
    with session_factory()() as db:
        db.get(TranslationProvider, case.provider).quota_multiplier = Decimal('.1')
        for index, amount in enumerate(('.3', '.2', '.05')):
            db.add(QuotaPeriod(id=f'fraction-{index}', owner_id=case.owner, kind='classic_grant', mode='classic',
                source='grant', source_key=f'fraction-{index}', starts_at=now() - timedelta(days=1),
                granted=Decimal(amount), used=0, reserved=0))
        db.commit()
    key = create_document(case)
    job_id = prepare(case, key, 6)
    assert not reserve(job_id)
    snapshot = case.client.get('/v1/documents/' + key, headers=case.auth).json()
    assert (snapshot['quota']['needed'], snapshot['quota']['available'], snapshot['quota']['difference']) == (.6, .5, .1)
    with session_factory()() as db:
        assert db.scalar(select(func.sum(QuotaPeriod.reserved))) == 0
        assert db.scalar(select(func.count()).select_from(DocumentQuotaAllocation)) == 0
        db.get(QuotaPeriod, 'fraction-1').granted = Decimal('.3')
        db.commit()
    assert reserve(job_id)
    with session_factory()() as db:
        assert db.scalar(select(func.count()).select_from(DocumentQuotaAllocation)) == 2
        for page in db.scalars(select(DocumentPage).where(DocumentPage.job_id == job_id)):
            page.status, page.billable = 'translated', True
        settle_document(db, db.get(Job, job_id))
        db.commit()
        assert db.scalar(select(func.sum(QuotaPeriod.used))) == Decimal('.6')
        assert db.get(QuotaPeriod, 'fraction-2').used == 0
