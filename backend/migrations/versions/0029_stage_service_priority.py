"""Index the frozen service tier of runnable stages without altering receipts or leases."""
from alembic import op
import sqlalchemy as sa

revision = 'stage_service_priority_0029'
down_revision = 'quota_multipliers_0028'
branch_labels = depends_on = None


def upgrade():
    op.add_column('job_stages', sa.Column('service_priority', sa.SmallInteger(), nullable=False, server_default='0'))
    jobs = sa.table('jobs', sa.column('id'), sa.column('status'), sa.column('entitlement', sa.JSON()))
    stages = sa.table('job_stages', sa.column('job_id'), sa.column('service_priority'))
    rights = jobs.c.entitlement
    active = ('uploading', 'awaiting_upload', 'validating_upload', 'preflight', 'awaiting_quota',
              'queued', 'running', 'outcome_unknown')
    op.execute(stages.update().where(stages.c.job_id == jobs.c.id, jobs.c.status.in_(active),
        sa.or_(rights['plan'].as_string().not_in(('free', 'guest')), rights['priority'].as_integer() > 0))
        .values(service_priority=1))
    ready = sa.text("status = 'ready'")
    op.create_index('ix_stage_service_ready', 'job_stages', ['name', sa.text('service_priority DESC'), 'available_at', 'id'],
                    sqlite_where=ready, postgresql_where=ready)


def downgrade():
    op.drop_index('ix_stage_service_ready', table_name='job_stages')
    op.drop_column('job_stages', 'service_priority')
