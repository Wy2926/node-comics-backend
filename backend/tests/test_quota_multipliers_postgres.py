"""Run the same fractional quota regressions against the dedicated PostgreSQL DB."""
import os
import pytest
from test_postgres_concurrency import pg_scope  # noqa: F401
from test_compute_v3_postgres import client  # noqa: F401
from test_translation_providers import admin_case  # noqa: F401
from test_document_quota_api import document_case  # noqa: F401
from test_quota_multipliers import (
    test_decimal_tenths_exhaust_exactly_and_summary_keeps_fractions,
    test_fractional_last_balance_is_atomic,
    test_fractional_grant_and_compensation_are_idempotent,
    test_document_freezes_rate_and_settles_billable_pages_only,
    test_fractional_document_buckets_reserve_all_or_none,
)

pytestmark = pytest.mark.skipif(os.environ.get('RUN_POSTGRES_CONCURRENCY') != '1',
                               reason='Requires the dedicated nodecomics_concurrency_test database')


def test_postgres_upgrade_preserves_active_reservations(pg_scope):
    from app.db import engine
    from test_quota_multipliers_migration import test_upgrade_preserves_inflight_image_and_document_reservations
    test_upgrade_preserves_inflight_image_and_document_reservations(engine())
