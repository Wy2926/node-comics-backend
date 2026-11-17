"""Independent model rates and exact fractional quota accounting; old rates stay 1."""
from importlib import import_module
import sqlalchemy as sa

revision = 'quota_multipliers_0028'
down_revision = 'whole_page_text_0027'
branch_labels = depends_on = None
_table = import_module('migrations.versions.0014_quota_purchases')._table


def upgrade():
    for table, columns in (
        ('quota_periods', ('granted', 'used', 'reserved')),
        ('usage_ledger', ('amount',)),
        ('jobs', ('quota_pages',)),
        ('translation_providers', ()),
        ('document_quota_allocations', ('reserved', 'used', 'released')),
    ):
        replacing = (lambda sql: all(part in sql for part in ('granted', 'used', 'reserved', '>=')),) if table == 'quota_periods' else ()
        with _table(table, replacing) as batch:
            for column in columns:
                batch.alter_column(column, existing_type=sa.Integer(), type_=sa.Numeric(18, 4), existing_nullable=False)
            if table == 'quota_periods':
                batch.create_check_constraint('ck_quota_balance', 'granted >= round(used + reserved, 4)')
            if table in ('translation_providers', 'jobs', 'document_quota_allocations'):
                batch.add_column(sa.Column('quota_multiplier', sa.Numeric(18, 4), nullable=False, server_default='1'))
                name = {'translation_providers': 'provider', 'jobs': 'job', 'document_quota_allocations': 'document'}[table]
                batch.create_check_constraint(f'ck_{name}_quota_multiplier', 'quota_multiplier > 0 AND quota_multiplier <= 10000')
            if table == 'document_quota_allocations':
                batch.drop_constraint('ck_document_allocation_balance', type_='check')
                batch.create_check_constraint('ck_document_allocation_balance',
                    'pages BETWEEN 1 AND 200 AND reserved >= 0 AND used >= 0 AND released >= 0 '
                    'AND round(pages * quota_multiplier, 4) = round(reserved + used + released, 4)')


def downgrade():
    raise RuntimeError('Fractional quotas cannot be truncated; restore a consistent backup to roll back')
