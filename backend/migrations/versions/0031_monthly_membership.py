"""Allow prepaid calendar months independently of automatic renewal."""
from importlib import import_module

revision = 'monthly_membership_0031'
down_revision = 'failed_text_output_0030'
branch_labels = None
depends_on = None
_table = import_module('migrations.versions.0014_quota_purchases')._table


def upgrade():
    with _table('billing_prices') as batch:
        batch.drop_constraint('ck_billing_price_type', type_='check')
        batch.create_check_constraint('ck_billing_price_type',
            "billing_type IN ('recurring', 'onetime') AND (interval != 'once' OR billing_type = 'onetime')")


def downgrade():
    raise RuntimeError('Monthly memberships require matching payment code; restore a consistent backup to roll back')
