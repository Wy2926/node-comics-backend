"""Opt-in plan routing and migration against a dedicated PostgreSQL test schema."""
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier

from sqlalchemy import func, select

from app.db import session_factory
from app.models import Job
from conftest import login, login_plus
from test_cluster_submissions import descriptor, submit
from test_postgres_concurrency import pytestmark
from test_translation_providers import PATH, admin_case
from test_translation_plan_routing import supplier


def test_scope_edit_serializes_with_new_admission(admin_case):
    client, admin = admin_case
    provider = supplier(admin_case, ['free'])
    reader = login(client)
    premium = login_plus(client, 'premium')
    ready = Barrier(2)
    def edit():
        ready.wait(timeout=10)
        return client.put(PATH + '/' + provider['id'], headers=admin, json={
            **{key: provider[key] for key in ('name', 'channel', 'enabled', 'text_weight', 'title_weight', 'config')},
            'text_plan_ids': ['plus']})
    def accept():
        ready.wait(timeout=10)
        return submit(client, reader, descriptor(b'racing'), key='racing')
    with ThreadPoolExecutor(max_workers=2) as pool:
        edited, accepted = pool.submit(edit), pool.submit(accept)
        assert edited.result().status_code == 200
        status = accepted.result().status_code
        assert status in (202, 503)
    assert submit(client, reader, descriptor(b'after'), key='after').status_code == 503
    assert submit(client, premium, descriptor(b'after'), key='premium').status_code == 202
    with session_factory()() as db:
        assert db.scalar(select(func.count()).select_from(Job)) == (2 if status == 202 else 1)
        jobs = db.scalars(select(Job)).all()
        assert all(job.config['text']['provider_id'] == provider['id'] for job in jobs)
        assert {job.entitlement['plan'] for job in jobs} == ({'free', 'plus'} if status == 202 else {'plus'})


def test_idle_worker_query_does_not_compare_json_plan_scopes(admin_case):
    from app.scheduler import next_control_delay
    supplier(admin_case, ['free'])
    supplier(admin_case, ['plus'])
    with session_factory()() as db:
        # PostgreSQL rejects DISTINCT over JSON even when the queue is empty.
        assert next_control_delay(db) == 5
