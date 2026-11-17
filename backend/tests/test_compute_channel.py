"""Duplex dispatch, scoped changes, durable replay and idle resource release."""
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta

import pytest
from sqlalchemy import func, select
from starlette.websockets import WebSocketDisconnect

from app import compute_channel
from app.db import engine, session_factory
from app.models import Job, now
from app.notifications import hub
from app.queue_models import ComputeClaim, ComputeNode, ExecutionLease, JobStage
from test_compute_v3 import v3, analyze, claim, request, text as complete_text
from test_compute_multinode import peer
from test_document_compute import documents, create, complete
from test_document_quota_api import document_case
from test_node_scheduling import configure
from test_notifications import wait_for_idle_subscriptions


def connect(node):
    return node['client'].websocket_connect(
        f'/internal/compute/v3/nodes/{node["node"]["node_id"]}/channel',
        headers=node['auth'], subprotocols=['compute.v3'])


def ready(socket, request_id='offer', count=4, document_count=0, version=1):
    socket.send_json({'type': 'ready', 'request_id': request_id,
        'config_version': version, 'count': count, 'document_count': document_count})


def assignment(socket):
    while True:
        value = socket.receive_json()
        if value['type'] == 'assignment':
            return value


def test_idle_offer_pushes_new_work_without_polling_and_requires_ack(v3):
    with connect(v3) as socket:
        assert socket.accepted_subprotocol == 'compute.v3'
        assert socket.receive_json()['config']['version'] == 1
        ready(socket)
        wait_for_idle_subscriptions(1)
        assert engine().pool.checkedout() == 0
        with session_factory()() as db:
            assert db.scalar(select(func.count()).select_from(ComputeClaim)) == 0
        jobs = v3['create']()
        grant = assignment(socket)
        assert {item['job_id'] for item in grant['leases']} == set(jobs)
        socket.send_json({'type': 'ack', 'request_id': 'offer'})
        ready(socket, 'next')
        wait_for_idle_subscriptions(1)
    assert not hub().waiters
    with session_factory()() as db:
        assert db.get(ComputeNode, v3['node']['node_id']).runtime_report['dispatch_slots'] == 0
        assert db.scalar(select(func.count()).select_from(ExecutionLease)) == 1
    assert request(v3, f'/nodes/{v3["node"]["node_id"]}/updates', {}).status_code == 404


def test_disconnect_before_ack_replays_only_original_leases(v3):
    v3['create'](3)
    with connect(v3) as socket:
        ready(socket, count=2)
        first = assignment(socket)
    with connect(v3) as socket:
        ready(socket, count=2)
        replay = assignment(socket)
        assert [row['lease_id'] for row in replay['leases']] == [row['lease_id'] for row in first['leases']]
        socket.send_json({'type': 'ack', 'request_id': 'offer'})
        ready(socket, 'next', count=2)
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
        ready(socket)
        first = assignment(socket) if assigned else None
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
        ready(socket)
        replay = assignment(socket)
        assert len(replay['leases']) == 1
        assert replay['leases'][0]['status'] == 'active'
        if first:
            assert replay['leases'][0]['lease_id'] == first['leases'][0]['lease_id']
    with session_factory()() as db:
        assert db.scalar(select(func.count()).select_from(ExecutionLease)) == 1


def test_same_offer_before_ack_replays_but_changed_content_conflicts(v3):
    v3['create'](2)
    with connect(v3) as socket:
        ready(socket, count=1)
        first = assignment(socket)
        ready(socket, count=1)
        assert assignment(socket)['leases'] == first['leases']
        ready(socket, count=2)
        reply = socket.receive_json()
        assert reply['type'] == 'error' and reply['code'] == 'CLAIM_CONFLICT'
    with session_factory()() as db:
        assert db.scalar(select(func.count()).select_from(ExecutionLease)) == 1


def test_pending_offer_renews_capacity_without_creating_receipts(v3):
    identity = v3['node']['node_id']
    credential, connection = compute_channel.authenticated(identity, v3['auth']['Authorization'], identity)
    body = compute_channel.Ready(type='ready', request_id='idle', config_version=1, count=4, document_count=0)
    compute_channel.offer(identity, credential, connection, body)
    with session_factory()() as db:
        node = db.get(ComputeNode, identity)
        node.runtime_report = {**node.runtime_report, 'dispatch_until': '2000-01-01T00:00:00Z'}
        db.commit()
    assert compute_channel.assign(identity, credential, connection, body) is None
    with session_factory()() as db:
        report = db.get(ComputeNode, identity).runtime_report
        assert report['dispatch_slots'] == 4 and report['dispatch_until'] > compute_channel.stamp(now())
        assert db.scalar(select(func.count()).select_from(ComputeClaim)) == 0


@pytest.mark.parametrize('binary', [False, True])
def test_oversized_or_binary_frames_close_and_release_offer(v3, binary):
    with connect(v3) as socket:
        socket.receive_json()
        ready(socket)
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


def test_external_cancellation_while_joining_idle_waiter_releases_resources(v3, monkeypatch):
    import asyncio
    import anyio
    from types import SimpleNamespace
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
        with anyio.CancelScope() as cancellation:
            async def disconnect(*args, **kwargs):
                result = await asyncio.wait(*args, **kwargs)
                cancellation.cancel()
                return result
            monkeypatch.setattr(compute_channel, 'asyncio',
                SimpleNamespace(**{**vars(asyncio), 'wait': disconnect}))
            socket = compute_channel.WebSocket({'type': 'websocket', 'subprotocols': ['compute.v3'],
                'headers': [(key.lower().encode(), value.encode()) for key, value in v3['auth'].items()]},
                receive, send)
            await compute_channel.channel(socket, v3['node']['node_id'])
    monkeypatch.setattr(compute_channel, 'RECONCILE_SECONDS', .01)
    anyio.run(run)
    assert frames[-1]['type'] == 'websocket.close'
    assert not hub().waiters and engine().pool.checkedout() == 0
    with session_factory()() as db:
        assert db.get(ComputeNode, v3['node']['node_id']).runtime_report['dispatch_slots'] == 0


def test_replay_still_works_after_disable_and_config_change(v3):
    v3['create']()
    with connect(v3) as socket:
        ready(socket)
        first = assignment(socket)
    with session_factory()() as db:
        node = db.get(ComputeNode, v3['node']['node_id'])
        node.enabled, node.config_version = False, 2
        db.commit()
    with connect(v3) as socket:
        ready(socket)
        assert assignment(socket)['leases'][0]['lease_id'] == first['leases'][0]['lease_id']
        socket.send_json({'type': 'ack', 'request_id': 'offer'})
        ready(socket, 'old-config')
        error = socket.receive_json()
        assert error['type'] == 'error' and error['code'] == 'NODE_CONFIG_CONFLICT'


def test_parallel_replayed_offer_allocates_once(v3):
    v3['create'](5)
    credential, connection = compute_channel.authenticated(v3['node']['node_id'],
        v3['auth']['Authorization'], v3['node']['node_id'])
    body = compute_channel.Ready(type='ready', request_id='parallel', config_version=1, count=4, document_count=0)
    with ThreadPoolExecutor(3) as pool:
        replies = list(pool.map(lambda _: compute_channel.assign(
            v3['node']['node_id'], credential, connection, body), range(3)))
    ids = [[item['lease_id'] for item in reply['leases']] for reply in replies]
    assert len(ids[0]) == 4 and ids[0] == ids[1] == ids[2]


@pytest.mark.parametrize('message', [
    {'type': 'ready', 'request_id': 'x', 'config_version': 1, 'count': 5, 'document_count': 0},
    {'type': 'ready', 'request_id': 'x', 'config_version': 1, 'count': True, 'document_count': 0},
    {'type': 'ack', 'request_id': 'unassigned'}, {'type': 'unknown'},
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
    with connect(v3) as socket:
        socket.receive_json()
        path = f'/v1/admin/compute-nodes/{v3["node"]["node_id"]}/rotate-credential'
        assert v3['client'].post(path, headers=v3['admin']).status_code == 200
        with pytest.raises(WebSocketDisconnect):
            socket.receive_json()


def test_text_arrival_and_terminal_are_pushed_on_same_connection_without_renewal(v3):
    v3['create']()
    lease = claim(v3).json()['leases'][0]
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
    lease = claim(v3).json()['leases'][0]
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


def test_missed_notice_is_reconciled_without_node_poll(v3, monkeypatch):
    monkeypatch.setattr(compute_channel, 'RECONCILE_SECONDS', .05)
    with connect(v3) as socket:
        socket.receive_json()
        ready(socket)
        wait_for_idle_subscriptions(1)
        from app import notifications
        monkeypatch.setattr(notifications, 'publish', lambda *_: None)
        v3['create']()
        assert len(assignment(socket)['leases']) == 1


def test_backpressured_strong_peer_does_not_hold_weaker_nodes_work(v3):
    strong = peer(v3)
    configure(strong, 100)
    with connect(strong) as strong_socket, connect(v3) as weak_socket:
        strong_socket.receive_json()
        weak_socket.receive_json()
        # The strong GPU has no local admission capacity and offers no slots.
        v3['create']()
        ready(weak_socket)
        assert len(assignment(weak_socket)['leases']) == 1


def test_expired_offer_does_not_block_even_with_live_http_heartbeat(v3):
    strong = peer(v3)
    configure(strong, 100)
    identity = strong['node']['node_id']
    credential, connection = compute_channel.authenticated(identity, strong['auth']['Authorization'], identity)
    body = compute_channel.Ready(type='ready', request_id='orphan', config_version=1, count=4, document_count=0)
    compute_channel.offer(identity, credential, connection, body)
    with session_factory()() as db:
        node = db.get(ComputeNode, identity)
        node.runtime_report = {**node.runtime_report, 'dispatch_until': '2000-01-01T00:00:00Z'}
        node.heartbeat_at = now()
        db.commit()
    v3['create']()
    with connect(v3) as socket:
        ready(socket)
        assert len(assignment(socket)['leases']) == 1


def test_registered_channel_node_without_upgrade_does_not_reserve_work(v3):
    strong = peer(v3)
    configure(strong, 100)
    assert request(strong, '/nodes/register', {
        **strong['registration'], 'control_channel': 'websocket'}).status_code == 200
    v3['create']()
    with connect(v3) as socket:
        ready(socket)
        assert len(assignment(socket)['leases']) == 1


def test_new_connection_fences_old_connection_without_clearing_new_offer(v3):
    with connect(v3) as old:
        old.receive_json()
        ready(old)
        with connect(v3) as current:
            current.receive_json()
            with pytest.raises(WebSocketDisconnect):
                old.receive_json()
            ready(current, 'new-connection')
            v3['create']()
            assert len(assignment(current)['leases']) == 1


def test_documents_share_capacity_offers_and_keep_workspace_affinity(documents):
    job_id = create(documents, prepared=False)
    with connect(documents.v3) as socket:
        ready(socket, document_count=1)
        lease = assignment(socket)['leases'][0]
        assert lease['job_id'] == job_id and lease['action'] == 'doc_prepare'
        socket.send_json({'type': 'ack', 'request_id': 'offer'})
        assert complete(documents, lease).status_code == 200
        ready(socket, 'ocr', document_count=1)
        assert assignment(socket)['leases'][0]['action'] == 'doc_ocr'
