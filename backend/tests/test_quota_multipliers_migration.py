"""Existing balances, active reservations and ledger IDs survive the numeric cutover."""
from datetime import timedelta
from decimal import Decimal
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.documents.models import Document, DocumentPage, DocumentQuotaAllocation
from app.documents.quota import settle_document
from app.entitlement_models import QuotaPeriod
from app.entitlements import settle
from app.models import Job, Ledger, User, now
from app.translation_models import TranslationProvider
from test_job_results_migration import before_quota_multiplier, migrate
from test_request_limits_migration import isolated_migration_database  # noqa: F401


def test_upgrade_preserves_inflight_image_and_document_reservations(isolated_migration_database):
    engine = isolated_migration_database
    migrate(engine, 'whole_page_text_0027')
    with Session(engine) as db:
        db.add(User(id='owner', subject='owner', name='owner'))
        db.flush()
        db.add(QuotaPeriod(id='balance', owner_id='owner', kind='classic_grant', mode='classic',
            source='grant', source_key='old-grant', granted=10, used=3, reserved=3,
            starts_at=now() - timedelta(days=1)))
        db.flush()
        db.execute(before_quota_multiplier(TranslationProvider).insert().values(
            id='provider', name='old', channel='openai'))
        for workflow, amount in [('image', 1), ('document', 2)]:
            db.execute(before_quota_multiplier(Job).insert().values(id=workflow, owner_id='owner',
                mode='classic', workflow_kind=workflow, target_language='en', operation=workflow,
                idempotency_key=workflow, request_hash='a' * 64, cache_key='b' * 64, config={},
                quota_kind='classic_grant' if workflow == 'image' else 'document_pages', quota_pages=amount,
                quota_period_id='balance' if workflow == 'image' else None, settlement='reserved', status='running'))
            db.add(Ledger(id=workflow, owner_id='owner', job_id=workflow, period_id='balance',
                transaction_key=workflow + ':reserve', kind='reserve', amount=amount))
        db.add(Document(job_id='document', filename='old.zip', source_type='zip', source_key='old/source',
            source_size=100, source_sha256='c' * 64, page_count=2, reserved_at=now(),
            expires_at=now() + timedelta(days=1)))
        db.flush()
        db.execute(before_quota_multiplier(DocumentQuotaAllocation).insert().values(
            id='allocation', job_id='document', period_id='balance', pages=2, reserved=2,
            entitlement={'kind': 'classic_grant'}))
        for index in (1, 2):
            db.add(DocumentPage(job_id='document', page_index=index, allocation_id='allocation',
                status='translated', billable=True))
        db.commit()
    migrate(engine, 'head')
    migrate(engine, 'head')
    with Session(engine) as db:
        bucket = db.get(QuotaPeriod, 'balance')
        assert (bucket.granted, bucket.used, bucket.reserved) == (Decimal(10), Decimal(3), Decimal(3))
        assert db.get(TranslationProvider, 'provider').quota_multiplier == 1
        assert db.get(DocumentQuotaAllocation, 'allocation').quota_multiplier == 1
        image, document = db.get(Job, 'image'), db.get(Job, 'document')
        assert image.quota_multiplier == document.quota_multiplier == 1
        settle(db, image, success=False)
        settle_document(db, document)
        db.commit()
        assert (bucket.granted, bucket.used, bucket.reserved) == (10, 5, 0)
        assert db.get(Ledger, 'image').amount == 1 and db.get(Ledger, 'document').amount == 2
        assert list(db.scalars(select(Ledger.amount).where(Ledger.kind == 'release'))) == [1]
