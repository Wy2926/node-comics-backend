"""Backfill in-flight stages from their receipts, never from current account state."""
from sqlalchemy import insert, select
from sqlalchemy.orm import Session

from app.models import Job, User
from app.queue_models import JobStage
from test_job_results_migration import migrate
from test_request_limits_migration import isolated_migration_database  # noqa: F401


def test_upgrade_keeps_frozen_entitlements(isolated_migration_database):
    engine = isolated_migration_database
    migrate(engine, 'quota_multipliers_0028')
    with Session(engine) as db:
        db.add(User(id='reader', subject='reader', name='reader'))
        db.flush()
        cases = [('legacy', {'plan': 'lite'}, 'text', 1), ('document', {'plan': 'plus', 'priority': 0}, 'doc_prepare', 1),
                 ('purchase', {'plan': 'free', 'priority': 1}, 'validate_upload', 1),
                 ('guest', {'plan': 'guest'}, 'page', 0), ('ordinary', {}, 'page', 0)]
        for key, rights, stage, _ in cases:
            db.execute(insert(Job.__table__).values(id=key, owner_id='reader', entitlement=rights, target_language='en', mode='classic',
                workflow_kind='document' if key == 'document' else 'image', operation='translate', idempotency_key=key,
                status='running', quota_pages=0, quota_kind='fixture', config={}, cache_key='c', request_hash='r'))
            db.execute(insert(JobStage.__table__).values(id=key, job_id=key, name=stage, status='waiting', generation=3))
        db.commit()
    migrate(engine, 'head')
    with Session(engine) as db:
        for key, rights, _, rank in cases:
            stage = db.get(JobStage, key)
            assert (stage.service_priority, stage.generation, stage.status) == (rank, 3, 'waiting')
            assert db.scalar(select(Job.entitlement).where(Job.id == key)) == rights
