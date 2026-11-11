"""Retiring grouping preserves immutable credentials and paid job checkpoints."""
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import ClassicState, Job, User
from app.translation_models import TranslationProvider, TranslationProviderRevision
from app.translation_providers import resolve_credentials
from test_job_results_migration import migrate
from test_request_limits_migration import isolated_migration_database  # noqa: F401


def test_upgrade_rotates_only_grouped_active_revisions(isolated_migration_database):
    engine = isolated_migration_database
    migrate(engine, 'cleanup_indexes_0026')
    with Session(engine) as db:
        db.add(User(id='reader', subject='reader', name='test'))
        # More than one bounded migration batch, including disabled suppliers.
        for index in range(105):
            key = f'provider-{index:03}'
            db.add(TranslationProvider(id=key, name=key, channel='openai', enabled=index != 1,
                revision_id=key, requests_per_minute=60))
        db.flush()
        for index in range(105):
            key = f'provider-{index:03}'
            db.add(TranslationProviderRevision(id=key, provider_id=key, channel='openai',
                config={'model': 'frozen', 'group_bytes': 1800}, api_key='isolated-migration-key'))
        db.add(TranslationProviderRevision(id='historical', provider_id='provider-000', channel='openai',
            config={'model': 'older', 'group_bytes': 128}, api_key='isolated-old-key'))
        db.flush()
        profile = {'provider_id': 'provider-000', 'revision_id': 'provider-000', 'channel': 'openai',
                   'model': 'frozen', 'group_bytes': 1800}
        db.add(Job(id='existing', owner_id='reader', mode='classic', target_language='en', status='running',
            config={'text': profile}, operation='translation', idempotency_key='existing',
            request_hash='a' * 64, cache_key='b' * 64, quota_pages=1, quota_kind='classic_free'))
        db.flush()
        db.add(ClassicState(job_id='existing', analysis={'segments': []}, translations={'old': 'paid'}))
        db.commit()
        before = [db.execute(select(model.__table__)).all() for model in (Job, ClassicState, TranslationProviderRevision)]
    migrate(engine, 'head')
    migrate(engine, 'head')
    with Session(engine) as db:
        assert [db.execute(select(model.__table__)).all() for model in (Job, ClassicState)] == before[:2]
        for old in before[2]:
            assert db.execute(select(TranslationProviderRevision.__table__).where(
                TranslationProviderRevision.id == old.id)).one() == old
        providers = db.scalars(select(TranslationProvider)).all()
        assert len(providers) == 105
        assert all(db.get(TranslationProviderRevision, provider.revision_id).config == {'model': 'frozen'} for provider in providers)
        assert not db.get(TranslationProvider, 'provider-001').enabled
        assert len(db.scalars(select(TranslationProviderRevision)).all()) == 211
    assert resolve_credentials(profile) == 'isolated-migration-key'
