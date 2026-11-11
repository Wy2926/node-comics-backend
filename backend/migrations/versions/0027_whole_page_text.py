"""Retire grouping from active provider revisions without rewriting frozen calls/jobs."""
from datetime import datetime, timezone
from uuid import uuid4

from alembic import op
import sqlalchemy as sa

revision = 'whole_page_text_0027'
down_revision = 'cleanup_indexes_0026'
branch_labels = depends_on = None


def upgrade():
    db = op.get_bind()
    providers = sa.table('translation_providers', sa.column('id', sa.String()), sa.column('revision_id', sa.String()))
    revisions = sa.table('translation_provider_revisions',
        sa.column('id', sa.String()), sa.column('provider_id', sa.String()), sa.column('channel', sa.String()),
        sa.column('config', sa.JSON()), sa.column('api_key', sa.Text()), sa.column('created_at', sa.DateTime()))
    cursor = ''
    while rows := db.execute(sa.select(revisions).join(providers, providers.c.revision_id == revisions.c.id)
            .where(providers.c.id > cursor).order_by(providers.c.id).limit(100)).mappings().all():
        for row in rows:
            if 'group_bytes' not in row['config']:
                continue
            new_id = str(uuid4())
            db.execute(revisions.insert().values(id=new_id, provider_id=row['provider_id'], channel=row['channel'],
                config={key: value for key, value in row['config'].items() if key != 'group_bytes'},
                api_key=row['api_key'], created_at=datetime.now(timezone.utc).replace(tzinfo=None)))
            db.execute(providers.update().where(providers.c.id == row['provider_id']).values(revision_id=new_id))
        cursor = rows[-1]['provider_id']


def downgrade():
    raise RuntimeError('Whole-page text requires restoring a pre-upgrade database backup for rollback')
