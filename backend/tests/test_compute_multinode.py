"""Multi-node claims, bounded contention recovery and identity fencing."""
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta

from sqlalchemy import func, select

from app import dispatcher
from app.db import session_factory
from app.models import Job, now
from app.queue_models import ComputeNode, ExecutionLease, JobStage
from test_compute_claims import synchronized_preparation
from test_compute_v3 import PREFIX, analysis_for, analyze, claim, heartbeat, request, read_input, v3
from test_node_management import provision


def peer(v3, resource='peer:gpu:0', *, language='en'):
    node, auth, admin = provision(v3['client'], resource)
    registration = {**v3['registration'], 'resource_id': resource, 'supported_languages': [language]}
    response = v3['client'].post(PREFIX + '/nodes/register', headers=auth, json=registration)
    assert response.status_code == 200, response.text
    return {**v3, 'node': node, 'auth': auth, 'admin': admin, 'registration': registration}


def test_nodes_claim_with_independent_capacity_and_receipts(v3):
    other = peer(v3)
    jobs = set(v3['create'](6))
    # Both nodes may use the same local request ID; each owns its own receipt.
    first = claim(v3, 4, 'same-local-id')
    second = claim(other, 4, 'same-local-id')
    assert len(first) == 4 and len(second) == 1
    assert len({item['job_id'] for item in first + second}) == 5
    assert {item['job_id'] for item in first + second} < jobs
    assert claim(v3) == [] and claim(other) == []
    analyze(other, second[0], empty=True)
    assert len(claim(other)) == 1
    assert [item['lease_id'] for item in claim(v3, 4, 'same-local-id')] == [item['lease_id'] for item in first]
    assert claim(other, 4, 'same-local-id')[0]['status'] == 'terminal'


def test_three_simultaneous_nodes_fill_slots_despite_shared_candidates(v3, monkeypatch):
    nodes = [v3, peer(v3), peer(v3, 'third:gpu:0')]
    jobs = set(v3['create'](6))
    synchronized_preparation(monkeypatch, 3)
    with ThreadPoolExecutor(max_workers=3) as pool:
        replies = list(pool.map(lambda node: claim(node, 1, 'race'), nodes))
    leases = [item for batch in replies for item in batch]
    assert len(leases) == len({item['job_id'] for item in leases}) == 3
    assert {item['job_id'] for item in leases} < jobs


def test_retry_preserves_missing_source_cleanup_and_releases_quota_once(v3):
    from app.models import Asset, Ledger
    from app.storage import LocalStore
    from app.upload_models import UploadReservation
    from app.uploads import expire_uploads
    missing, valid = v3['create'](2)
    with session_factory()() as db:
        source = db.get(Asset, db.get(Job, missing).input_asset_id)
        LocalStore().delete(source.storage_key)
    batch = claim(v3, 1, 'retire-missing')
    assert [item['job_id'] for item in batch] == [valid]
    assert claim(v3, 1, 'retire-missing')[0]['lease_id'] == batch[0]['lease_id']
    with session_factory()() as db:
        assert db.get(Job, missing).status == 'awaiting_upload'
        assert db.scalar(select(func.count()).select_from(Ledger).where(
            Ledger.job_id == missing, Ledger.kind == 'release')) == 0
        pending = db.scalar(select(UploadReservation).where(UploadReservation.job_id == missing))
        pending.expires_at = now() - timedelta(seconds=1)
        db.commit()
    for _ in range(2):
        with session_factory()() as db:
            expire_uploads(db)
            db.commit()
    with session_factory()() as db:
        assert db.get(Job, missing).status == 'failed'
        assert db.scalar(select(func.count()).select_from(Ledger).where(
            Ledger.job_id == missing, Ledger.kind == 'release')) == 1


def test_idle_or_incompatible_peer_never_reserves_work(v3):
    other = peer(v3, language='ja')
    v3['create'](3)
    assert claim(other) == []
    leases = claim(v3, 4)
    assert len(leases) == 3
    response = read_input(other, leases[0])
    assert response.status_code == 200
    assert response.content == read_input(v3, leases[0]).content
    assert heartbeat(other, [leases[0]]).json()['leases'][0]['status'] == 'stop'


def test_lost_node_work_recovers_on_peer_and_old_lease_cannot_publish(v3):
    other = peer(v3)
    v3['create']()
    first = claim(v3)[0]
    with session_factory()() as db:
        db.get(ExecutionLease, first['lease_id']).expires_at = now() - timedelta(seconds=1)
        db.get(ComputeNode, v3['node']['node_id']).heartbeat_at = now() - timedelta(hours=1)
        db.commit()
    dispatcher.recover_lease(first['lease_id'])
    dispatcher.recover_lease(first['lease_id'])
    with session_factory()() as db:
        stage = db.get(JobStage, db.get(ExecutionLease, first['lease_id']).stage_id)
        stage.available_at = now() - timedelta(seconds=1)
        db.commit()
    second = claim(other)[0]
    assert second['job_id'] == first['job_id'] and second['generation'] == first['generation'] + 1
    assert heartbeat(v3, [first]).json()['leases'][0]['status'] == 'terminal'
    response = read_input(v3, first)
    assert response.status_code == 200
    assert response.content == read_input(other, second).content
    from app.providers import digest
    stale_analysis = analysis_for(first)
    assert request(v3, f'/leases/{first["lease_id"]}/analysis', {
        'lease_token': first['lease_token'], 'analysis': stale_analysis,
        'analysis_hash': digest(stale_analysis)}).status_code == 409
    analyze(other, second, empty=True)
    with session_factory()() as db:
        from app.models import Ledger
        assert db.get(Job, first['job_id']).status == 'no_text'
        assert db.scalar(select(func.count()).select_from(Ledger).where(
            Ledger.job_id == first['job_id'], Ledger.kind == 'release')) == 1
