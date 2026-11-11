"""Additive cleanup indexes preserve task state and accept concurrently prebuilt indexes."""
import pytest
from sqlalchemy import inspect, select
from sqlalchemy.orm import Session

from app.models import Asset, Job, User
from test_job_results_migration import migrate
from test_request_limits_migration import isolated_migration_database  # noqa: F401


@pytest.mark.parametrize('prebuilt', [False, True])
def test_cleanup_upgrade_preserves_data(isolated_migration_database, prebuilt):
    engine = isolated_migration_database
    migrate(engine, 'auth_profiles_0025')
    with Session(engine) as db:
        db.add(User(id='cleanup-owner', subject='cleanup-owner', name='test'))
        db.flush()
        db.add_all([Asset(id=identity, owner_id='cleanup-owner', sha256='a' * 64, storage_key=identity,
            kind=kind, mime='image/png', width=10, height=10, byte_size=100)
            for identity, kind in [('cleanup-input', 'original'), ('cleanup-output', 'classic')]])
        db.flush()
        db.add(Job(id='cleanup-job', owner_id='cleanup-owner', input_asset_id='cleanup-input',
            output_asset_id='cleanup-output', mode='classic', target_language='en', status='running', phase='text',
            idempotency_key='cleanup-request', operation='translation', request_hash='a' * 64,
            cache_key='b' * 64, config={'immutable': 'checkpoint'}, quota_pages=1, quota_kind='classic_free'))
        db.commit()
        before = [db.execute(select(model.__table__)).all() for model in (Asset, Job)]
    expected = {'ix_assets_unpurged', 'ix_assets_live_created', 'ix_jobs_output_asset_id'}
    if prebuilt:
        for model in (Asset, Job):
            for index in model.__table__.indexes:
                if index.name in expected:
                    index.create(engine)
    migrate(engine, 'head')
    with Session(engine) as db:
        assert [db.execute(select(model.__table__)).all() for model in (Asset, Job)] == before
    assert expected <= {index['name'] for table in ('assets', 'jobs') for index in inspect(engine).get_indexes(table)}
