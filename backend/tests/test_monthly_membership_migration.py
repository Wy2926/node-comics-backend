"""The monthly-pass upgrade preserves prices and still rejects recurring page packs."""
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
import pytest
from test_job_results_migration import migrate
from test_request_limits_migration import isolated_migration_database  # noqa: F401


def test_monthly_pass_constraint_upgrade(isolated_migration_database):
    engine = isolated_migration_database
    migrate(engine, 'failed_text_output_0030')
    with engine.begin() as db:
        db.execute(text("INSERT INTO billing_plans (id,name,created_at) VALUES ('plus','PLUS',now())"))
        db.execute(text("INSERT INTO billing_plan_revisions "
            "(id,plan_id,version,name,monthly_classic_pages,trial_days,trial_classic_pages,created_at) "
            "VALUES ('plus-v1','plus',1,'PLUS',2500,0,0,now())"))
        db.execute(text("INSERT INTO billing_prices "
            "(id,plan_id,plan_revision_id,environment,currency,unit_amount,interval,billing_type,status,created_at) "
            "VALUES ('original','plus','plus-v1','test','usd',666,'quarter','onetime','archived',now())"))
    migrate(engine, 'head')
    migrate(engine, 'head')
    with engine.begin() as db:
        assert db.execute(text("SELECT unit_amount,interval,billing_type,status FROM billing_prices")).one() == (
            666, 'quarter', 'onetime', 'archived')
        db.execute(text("INSERT INTO billing_prices "
            "(id,plan_id,plan_revision_id,environment,currency,unit_amount,interval,billing_type,status,created_at) "
            "VALUES ('monthly','plus','plus-v1','test','usd',299,'month','onetime','draft',now())"))
    for interval, payment in [('once', 'recurring'), ('month', 'unknown'), ('week', 'onetime')]:
        with pytest.raises(IntegrityError):
            with engine.begin() as db:
                db.execute(text("UPDATE billing_prices SET interval=:interval,billing_type=:payment WHERE id='monthly'"),
                    dict(interval=interval, payment=payment))
