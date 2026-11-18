"""Keep private model output with its failed call, separate from deliverable checkpoints."""
from alembic import op
import sqlalchemy as sa

revision = 'failed_text_output_0030'
down_revision = 'stage_service_priority_0029'
branch_labels = depends_on = None


def upgrade():
    op.add_column('text_calls', sa.Column('failed_output', sa.JSON(none_as_null=True), nullable=True))
    kept = sa.text('failed_output IS NOT NULL')
    op.create_index('ix_text_calls_failed_output', 'text_calls', ['completed_at', 'id'],
                    postgresql_where=kept, sqlite_where=kept)


def downgrade():
    op.drop_index('ix_text_calls_failed_output', table_name='text_calls')
    op.drop_column('text_calls', 'failed_output')
