"""Duplex dispatch, scoped changes, durable replay and idle resource release."""
from datetime import timedelta

import pytest
from sqlalchemy import func, select
from starlette.websockets import WebSocketDisconnect

from app import compute_channel
from app.db import engine, session_factory
from app.models import Job, now
from app.notifications import hub
from app.queue_models import ComputeClaim, ComputeNode, ExecutionLease, JobStage
from test_compute_v3 import v3, analyze, heartbeat, text as complete_text
from test_compute_multinode import peer
from test_document_compute import documents, create
from test_document_quota_api import document_case
from test_node_scheduling import configure
from test_notifications import wait_for_idle_subscriptions
from compute_pull import assignment, body, claim, connect


def send_pull(socket, request_id='pull', count=4, document_count=0):
    socket.send_json(body(count, request_id, document_count=document_count))


def test_idle_hint_never_allocates_before_a_fresh_pull(v3):
    with connect(v3) as socket:
        assert socket.accepted_subprotocol == 'compute.pull.v1'
        assert socket.receive_json()['config']['version'] == 1
        send_pull(socket)
        assert assignment(socket)['leases'] == []
        wait_for_idle_subscriptions(1)
        assert engine().pool.checkedout() == 0
        with session_factory()() as db:
            assert db.scalar(select(func.count()).select_from(ComputeClaim)) == 0
        jobs = v3['create']()
        assert socket.receive_json() == {'type': 'updates', 'available': True}
        with session_factory()() as db:
            assert not db.scalar(select(ExecutionLease.id))
        send_pull(socket, 'take')
        grant = assignment(socket)
        assert {item['job_id'] for item in grant['leases']} == set(jobs)
        send_pull(socket, 'next')
        assert assignment(socket)['leases'] == []
        wait_for_idle_subscriptions(1)
    assert not hub().waiters
    with session_factory()() as db:
        assert db.get(ComputeNode, v3['node']['node_id']).runtime_report['dispatch_slots'] == 0
        assert db.scalar(select(func.count()).select_from(ExecutionLease)) == 1


def test_disconnect_after_pull_replays_only_original_leases(v3):
    v3['create'](3)
    with connect(v3) as socket:
        send_pull(socket, count=2)
        first = assignment(socket)
    with connect(v3) as socket:
        send_pull(socket, count=2)
        replay = assignment(socket)
        assert [row['lease_id'] for row in replay['leases']] == [row['lease_id'] for row in first['leases']]
        send_pull(socket, 'next', count=2)
        assert len(assignment(socket)['leases']) == 1
    with session_factory()() as db:
        assert db.scalar(select(func.count()).select_from(ExecutionLease)) == 3


@pytest.mark.parametrize('assigned', [False, True])
def test_channel_lifetime_releases_capacity_without_losing_pending_work(v3, monkeypatch, assigned):
    monkeypatch.setattr(compute_channel, 'CHANNEL_SECONDS', 1)
    if assigned:
        v3['create']()
    with connect(v3) as socket:
        socket.receive_json()
        send_pull(socket)
        first = assignment(socket)
        assert bool(first['leases']) == assigned
        with pytest.raises(WebSocketDisconnect) as error:
            socket.receive_json()
        assert error.value.code == 1001
    assert not hub().waiters and engine().pool.checkedout() == 0
    with session_factory()() as db:
        assert db.get(ComputeNode, v3['node']['node_id']).runtime_report['dispatch_slots'] == 0
        assert db.scalar(select(func.count()).select_from(ComputeClaim)) == int(assigned)
    if not assigned:
        v3['create']()
    with connect(v3) as socket:
        send_pull(socket)
        replay = assignment(socket)
        assert len(replay['leases']) == 1
        assert replay['leases'][0]['status'] == 'active'
        if assigned:
            assert replay['leases'][0]['lease_id'] == first['leases'][0]['lease_id']
    with session_factory()() as db:
        assert db.scalar(select(func.count()).select_from(ExecutionLease)) == 1


def test_same_pull_replays_but_changed_content_conflicts(v3):
    v3['create'](2)
    with connect(v3) as socket:
        send_pull(socket, count=1)
        first = assignment(socket)
        send_pull(socket, count=1)
        assert assignment(socket)['leases'] == first['leases']
        send_pull(socket, count=2)
        reply = socket.receive_json()
        assert reply['type'] == 'error' and reply['code'] == 'CLAIM_CONFLICT'
    with session_factory()() as db:
        assert db.scalar(select(func.count()).select_from(ExecutionLease)) == 1


def test_committed_pull_has_bounded_handoff_and_only_heartbeat_extends_it(v3):
    from app.compute_v3 import parsed
    from app.config import settings
    v3['create']()
    with connect(v3) as socket:
        send_pull(socket)
        lease = assignment(socket)['leases'][0]
        with session_factory()() as db:
            row = db.get(ExecutionLease, lease['lease_id'])
            assert 0 < (row.expires_at - row.started_at).total_seconds() < settings().cluster_lease_seconds
        send_pull(socket)
        assert assignment(socket)['leases'][0]['expires_at'] == lease['expires_at']
        renewed = heartbeat(v3, [lease]).json()['leases'][0]
        assert parsed(renewed['expires_at']) > parsed(lease['expires_at'])


def test_idle_snapshot_never_renews_advertised_capacity(v3):
    identity = v3['node']['node_id']
    connection = compute_channel.authenticated(identity, v3['auth']['Authorization'])
    body = compute_channel.Pull(request_id='idle', config_version=1, count=4, document_count=0)
    assert compute_channel.assign(identity, connection, body)['leases'] == []
    with session_factory()() as db:
        node = db.get(ComputeNode, identity)
        node.runtime_report = {**node.runtime_report, 'dispatch_until': '2000-01-01T00:00:00Z'}
        db.commit()
    assert not compute_channel.snapshot(identity, connection, set(), body)[-1]
    with session_factory()() as db:
        report = db.get(ComputeNode, identity).runtime_report
        assert report['dispatch_until'] == '2000-01-01T00:00:00Z'
        assert db.scalar(select(func.count()).select_from(ComputeClaim)) == 0


@pytest.mark.parametrize('binary', [False, True])
def test_oversized_or_binary_frames_close_and_release_capacity(v3, binary):
    with connect(v3) as socket:
        socket.receive_json()
        send_pull(socket)
        assert not assignment(socket)['leases']
        wait_for_idle_subscriptions(1)
        if binary:
            socket.send_bytes(b'{}')
        else:
            socket.send_text(' ' * 4097)
        with pytest.raises(WebSocketDisconnect) as error:
            socket.receive_json()
        assert error.value.code == (1003 if binary else 1009)
    with session_factory()() as db:
        assert db.get(ComputeNode, v3['node']['node_id']).runtime_report['dispatch_slots'] == 0


def test_send_timeout_closes_channel_and_releases_resources(v3, monkeypatch):
    import asyncio
    async def blocked(*_):
        await asyncio.Event().wait()
    monkeypatch.setattr(compute_channel.WebSocket, 'send_json', blocked)
    monkeypatch.setattr(compute_channel, 'SEND_SECONDS', .01)
    with connect(v3) as socket:
        with pytest.raises(WebSocketDisconnect):
            socket.receive_json()
    assert not hub().waiters and engine().pool.checkedout() == 0


def test_external_cancellation_while_idle_releases_resources(v3):
    import asyncio
    import anyio
    frames = []
    connected = False
    async def receive():
        nonlocal connected
        if not connected:
            connected = True
            return {'type': 'websocket.connect'}
        await asyncio.Event().wait()
    async def send(frame):
        frames.append(frame)
    async def run():
        async with anyio.create_task_group() as group:
            socket = compute_channel.WebSocket({'type': 'websocket', 'subprotocols': [compute_channel.SUBPROTOCOL],
                'headers': [(key.lower().encode(), value.encode()) for key, value in v3['auth'].items()]}, receive, send)
            group.start_soon(compute_channel.channel, socket, v3['node']['node_id'])
            with anyio.fail_after(3):
                while not any(frame['type'] == 'websocket.send' for frame in frames):
                    await anyio.sleep(.01)
            group.cancel_scope.cancel()
    anyio.run(run)
    assert frames[-1]['type'] == 'websocket.close'
    assert not hub().waiters and engine().pool.checkedout() == 0
    with session_factory()() as db:
        assert db.get(ComputeNode, v3['node']['node_id']).runtime_report['dispatch_slots'] == 0


def test_replay_still_works_after_disable_and_config_change(v3):
    v3['create']()
    with connect(v3) as socket:
        send_pull(socket)
        first = assignment(socket)
    with session_factory()() as db:
        node = db.get(ComputeNode, v3['node']['node_id'])
        node.enabled, node.config_version = False, 2
        db.commit()
    with connect(v3) as socket:
        send_pull(socket)
        assert assignment(socket)['leases'][0]['lease_id'] == first['leases'][0]['lease_id']
        send_pull(socket, 'old-config')
        error = socket.receive_json()
        assert error['type'] == 'error' and error['code'] == 'NODE_CONFIG_CONFLICT'


@pytest.mark.parametrize('message', [
    {'request_id': 'x', 'config_version': 1, 'count': 5, 'document_count': 0},
    {'request_id': 'x', 'config_version': 1, 'count': True, 'document_count': 0},
    {'request_id': 'x', 'config_version': 1, 'count': 0, 'document_count': 0},
])
def test_bad_frames_close_without_allocating(v3, message):
    with connect(v3) as socket:
        socket.receive_json()
        socket.send_json(message)
        with pytest.raises(WebSocketDisconnect) as error:
            socket.receive_json()
        assert error.value.code == 1008
    with session_factory()() as db:
        assert db.scalar(select(func.count()).select_from(ExecutionLease)) == 0


def test_channel_identity_and_credential_rotation(v3):
    wrong = {**v3, 'node': {'node_id': 'other'}}
    with pytest.raises(WebSocketDisconnect):
        with connect(wrong):
            pass
    with pytest.raises(WebSocketDisconnect):
        with connect(v3, 'unsupported'):
            pass
    with connect(v3) as socket:
        socket.receive_json()
        path = f'/v1/admin/compute-nodes/{v3["node"]["node_id"]}/rotate-credential'
        assert v3['client'].post(path, headers=v3['admin']).status_code == 200
        with pytest.raises(WebSocketDisconnect):
            socket.receive_json()


def test_text_arrival_and_terminal_are_pushed_on_same_connection_without_renewal(v3):
    v3['create']()
    lease = claim(v3)[0]
    analyze(v3, lease)
    with session_factory()() as db:
        expires = db.get(ExecutionLease, lease['lease_id']).expires_at
    with connect(v3) as socket:
        socket.receive_json()
        wait_for_idle_subscriptions(1)
        complete_text(lease)
        text = socket.receive_json()['leases'][0]
        assert text['translations']['translations'] == {'0': 'Hello'}
        with session_factory()() as db:
            assert db.get(ExecutionLease, lease['lease_id']).expires_at == expires
            job = db.get(Job, lease['job_id'])
            job.cancel_requested = True
            from app.scheduler import touch_job
            touch_job(db, job)
            db.commit()
        assert socket.receive_json()['leases'][0]['status'] == 'stop'


@pytest.mark.parametrize('stopped', ['expired', 'generation', 'deadline'])
def test_reconnect_snapshots_fenced_state_not_stale_translations(v3, stopped):
    v3['create']()
    lease = claim(v3)[0]
    analyze(v3, lease)
    complete_text(lease)
    with session_factory()() as db:
        row = db.get(ExecutionLease, lease['lease_id'])
        if stopped == 'expired':
            row.expires_at = now() - timedelta(seconds=1)
        elif stopped == 'generation':
            db.get(JobStage, row.stage_id).generation += 1
        else:
            row.limits = {**row.limits, 'deadline_at': (now() - timedelta(seconds=1)).isoformat() + 'Z'}
        db.commit()
    with connect(v3) as socket:
        socket.receive_json()
        reply = socket.receive_json()['leases'][0]
        assert reply['status'] == 'stop' and 'translations' not in reply


def test_missed_notice_is_reconciled_with_a_hint(v3, monkeypatch):
    monkeypatch.setattr(compute_channel, 'RECONCILE_SECONDS', .05)
    with connect(v3) as socket:
        socket.receive_json()
        send_pull(socket)
        assert assignment(socket)['leases'] == []
        wait_for_idle_subscriptions(1)
        from app import notifications
        monkeypatch.setattr(notifications, 'publish', lambda *_: None)
        v3['create']()
        assert socket.receive_json().get('available')
        send_pull(socket, 'after-hint')
        assert len(assignment(socket)['leases']) == 1


@pytest.mark.parametrize('connected', [False, True])
def test_strong_peer_without_advertised_capacity_does_not_block_work(v3, connected):
    from contextlib import nullcontext
    strong = peer(v3)
    configure(strong, 100)
    with connect(strong) if connected else nullcontext():
        v3['create']()
        assert len(claim(v3)) == 1


def test_expired_capacity_does_not_block_even_with_live_http_heartbeat(v3):
    strong = peer(v3)
    configure(strong, 100)
    identity = strong['node']['node_id']
    connection = compute_channel.authenticated(identity, strong['auth']['Authorization'])
    body = compute_channel.Pull(request_id='orphan', config_version=1, count=4, document_count=0)
    assert compute_channel.assign(identity, connection, body)['leases'] == []
    with session_factory()() as db:
        node = db.get(ComputeNode, identity)
        node.runtime_report = {**node.runtime_report, 'dispatch_until': '2000-01-01T00:00:00Z'}
        node.heartbeat_at = now()
        db.commit()
    v3['create']()
    assert len(claim(v3)) == 1


def test_fresh_preference_then_steal_with_live_unresponsive_peer(v3, monkeypatch):
    strong = peer(v3)
    configure(strong, 1000)
    monkeypatch.setattr(compute_channel, 'PREFERENCE_SECONDS', 60)
    with connect(strong) as fast, connect(v3) as healthy:
        send_pull(fast)
        assert assignment(fast)['leases'] == []
        jobs = v3['create'](2)
        send_pull(healthy)
        assert assignment(healthy)['leases'] == []
        with session_factory()() as db:
            stages = db.scalars(select(JobStage).where(JobStage.job_id.in_(jobs), JobStage.name == 'page'))
            for stage in stages:
                stage.available_at -= timedelta(seconds=61)
            db.commit()
        send_pull(healthy, 'steal')
        stolen = assignment(healthy)['leases']
        assert len(stolen) == 1
        send_pull(fast, 'caught-up')
        assert {x['job_id'] for x in stolen + assignment(fast)['leases']} == set(jobs)


def test_new_connection_fences_old_connection(v3):
    with connect(v3) as old:
        old.receive_json()
        send_pull(old)
        assert assignment(old)['leases'] == []
        with connect(v3) as current:
            current.receive_json()
            with pytest.raises(WebSocketDisconnect):
                old.receive_json()
            v3['create']()
            send_pull(current, 'new-connection')
            assert len(assignment(current)['leases']) == 1


def test_document_hint_does_not_choose_a_host_until_pull(documents):
    from app.documents.models import Document
    with connect(documents.v3) as socket:
        send_pull(socket, count=0, document_count=1)
        assert assignment(socket)['leases'] == []
        job_id = create(documents, prepared=False)
        assert socket.receive_json() == {'type': 'updates', 'available': True}
        with session_factory()() as db:
            assert db.get(Document, job_id).owner_node_id is None
            assert not db.scalar(select(ExecutionLease.id))
        send_pull(socket, 'take', count=0, document_count=1)
        lease, = assignment(socket)['leases']
        assert (lease['job_id'], lease['action'], lease['workflow_kind']) == (job_id, 'doc_prepare', 'document')
