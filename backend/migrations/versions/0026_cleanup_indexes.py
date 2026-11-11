"""Index pending file cleanup and result references instead of scanning history."""
from alembic import op
import sqlalchemy as sa

revision = 'cleanup_indexes_0026'
down_revision = 'auth_profiles_0025'
branch_labels = depends_on = None


def upgrade():
    # Large live databases may prebuild these indexes CONCURRENTLY before migration.
    for name, columns, predicate in (
        ('ix_assets_unpurged', ['kind', 'id'], 'purged_at IS NULL'),
        ('ix_assets_live_created', ['created_at', 'id'], "deleted_at IS NULL AND kind != 'original'"),
    ):
        op.create_index(name, 'assets', columns, sqlite_where=sa.text(predicate),
            postgresql_where=sa.text(predicate), if_not_exists=True)
    op.create_index('ix_jobs_output_asset_id', 'jobs', ['output_asset_id'], if_not_exists=True)


def downgrade():
    op.drop_index('ix_jobs_output_asset_id', table_name='jobs')
    op.drop_index('ix_assets_live_created', table_name='assets')
    op.drop_index('ix_assets_unpurged', table_name='assets')
