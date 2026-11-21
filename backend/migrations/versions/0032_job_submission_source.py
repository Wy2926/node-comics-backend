"""Record the submitting client on newly created translation jobs."""
from alembic import op
import sqlalchemy as sa

revision = 'job_submission_source_0032'
down_revision = 'monthly_membership_0031'
branch_labels = None
depends_on = None


def upgrade():
    op.add_column('jobs', sa.Column('submission_source', sa.String(16), nullable=True))


def downgrade():
    op.drop_column('jobs', 'submission_source')
