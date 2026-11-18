"""Pull dispatch: notifications advertise work; only a received request can lease it."""
import asyncio
from datetime import timedelta

import anyio
from fastapi import APIRouter, HTTPException, WebSocket, WebSocketDisconnect
from pydantic import Field, ValidationError, model_validator
from sqlalchemy import or_, select
from starlette.concurrency import run_in_threadpool
from starlette.websockets import WebSocketState

from .compute_v3 import config_payload, lease_payload, page_update, receipt, stamp
from .db import session_factory
from .errors import ProcessingError, problem
from .models import now, uid
from .node_auth import node_auth
from .notifications import hub
from .providers import digest
from .queue_models import ComputeClaim, ComputeNode, ExecutionLease, JobStage
from .request_models import RequestBody
from .scheduler import claim_batch, has_eligible_work, lock_scheduler, paid_reserve, prepare_claim_candidates, ready_page_priority

router = APIRouter(prefix='/internal/compute/v3')
RECONCILE_SECONDS = 5
PREFERENCE_SECONDS = 1
SUBPROTOCOL = 'compute.pull.v1'
SEND_SECONDS = 10
CHANNEL_SECONDS = 300  # Let reloaded proxies drain through normal node reconnection.


class Pull(RequestBody):
    request_id: str = Field(min_length=1, max_length=64, pattern=r'^[A-Za-z0-9_-]+$')
    config_version: int = Field(ge=1, strict=True)
    count: int = Field(ge=0, le=4, strict=True)
    document_count: int = Field(ge=0, le=1, strict=True)

    @model_validator(mode='after')
    def capacity(self):
        if not self.count and not self.document_count:
            raise ValueError('At least one execution slot is required')
        return self


def authenticated(node_id, authorization):
    with session_factory()() as db:
        lock_scheduler(db)
        node_auth(authorization, node_id, db)
        node = db.get(ComputeNode, node_id)
        if node.runtime_report.get('protocol_version') != 3:
            problem('PROTOCOL_MISMATCH', '请先注册整页协议', 409)
        connection = uid()
        node.runtime_report = {**node.runtime_report, 'channel_id': connection,
            'dispatch_slots': 0, 'dispatch_documents': 0}
        db.commit()
        return connection


def current_node(db, identity, connection):
    node = db.get(ComputeNode, identity, populate_existing=True)
    if not node or node.runtime_report.get('channel_id') != connection:
        problem('NODE_AUTH_REQUIRED', '计算节点连接已失效', 401)
    return node


def withdraw(identity, connection):
    with session_factory()() as db:
        lock_scheduler(db)
        node = current_node(db, identity, connection)
        node.runtime_report = {**node.runtime_report, 'dispatch_slots': 0, 'dispatch_documents': 0}
        db.commit()


def assign(identity, connection, body):
    """Only a received pull can allocate. Nonempty grants and their receipts commit together."""
    from .documents.compute import claim_document, has_document_work, payload
    with session_factory()() as db:
        key = (identity, body.request_id)
        prepared = (prepare_claim_candidates(db, identity, ['page'], limit=body.count)
                    if body.count and not db.get(ComputeClaim, key) else None)
        lock_scheduler(db)
        node = current_node(db, identity, connection)
        config = config_payload(node)
        saved = db.get(ComputeClaim, key, populate_existing=True)
        request_hash = digest(body.model_dump())
        if saved:
            if saved.request_hash != request_hash:
                problem('CLAIM_CONFLICT', '同一领取编号不能修改内容', 409)
            leases = [db.get(ExecutionLease, lease_id) for lease_id in saved.lease_ids]
        else:
            if body.config_version != node.config_version:
                problem('NODE_CONFIG_CONFLICT', '领取前需要同步配置', 409)
            cutoff = now() - timedelta(seconds=PREFERENCE_SECONDS)
            leases = (claim_batch(db, identity, ['page'], limit=body.count, prepared=prepared, steal_before=cutoff,
                                  higher_work=has_document_work)
                      if prepared else [])
            if not leases and body.document_count and (document := claim_document(db, node, steal_before=cutoff)):
                leases = [document]
            if leases:
                # Allow a request round trip and two heartbeats. Only actual renewal gets the full execution lease.
                handoff = now() + timedelta(seconds=max(config['request_seconds'] + SEND_SECONDS,
                    2 * config['heartbeat_seconds'] + 2))
                for lease in leases:
                    lease.expires_at = min(lease.expires_at, handoff)
                db.add(ComputeClaim(node_id=identity, request_id=body.request_id,
                    request_hash=request_hash, lease_ids=[lease.id for lease in leases]))
        # Only client traffic refreshes advertised capacity, never the notification loop.
        node.runtime_report = {**node.runtime_report, 'dispatch_slots': 0 if leases else body.count,
            'dispatch_documents': 0 if leases else body.document_count,
            'dispatch_until': stamp(now() + timedelta(seconds=config['poll_seconds'] + RECONCILE_SECONDS))}
        db.commit()
        return {'type': 'assignment', 'request_id': body.request_id, 'server_time': stamp(now()),
            'leases': [dict(workflow_kind='image', **lease_payload(db, lease)) if lease.resource_pool == 'image'
                       else dict(workflow_kind='document', **payload(db, lease))
                       for lease in leases]}


def snapshot(identity, connection, tracked, interest):
    from .documents.compute import NODE_STAGES, has_document_work, live
    with session_factory()() as db:
        node = current_node(db, identity, connection)
        rows = db.execute(select(ExecutionLease, JobStage.name, JobStage.service_priority).join(JobStage).where(
            ExecutionLease.node_id == identity,
            or_(ExecutionLease.completed_at.is_(None), ExecutionLease.id.in_(tracked)))).all()
        active = {lease.id for lease, _, _ in rows if not lease.completed_at}
        ordinary = sum(1 for lease, _, rank in rows if not lease.completed_at and not rank)
        replies = []
        for lease, stage, _ in rows:
            if stage == 'page':
                if reply := page_update(db, lease):
                    replies.append(reply)
                continue
            if stage not in NODE_STAGES:
                continue
            if lease.completed_at:
                replies.append(receipt(lease))
            else:
                try:
                    live(db, lease)
                except ProcessingError:
                    replies.append({'lease_id': lease.id, 'status': 'stop', 'code': 'LEASE_STOPPED'})
        # A hint doesn't reserve a stage or bind a document workspace.
        available = False
        if interest and node.enabled and interest.config_version == node.config_version and len(active) < node.capacity:
            if interest.count and 'page' in node.capabilities:
                minimum = int(ordinary >= node.capacity - paid_reserve(node))
                if not interest.document_count and 'doc_prepare' in node.capabilities:
                    rank = ready_page_priority(db, node, minimum)
                    available = rank is not None and (rank > 0 or not has_document_work(db, node, min_priority=1))
                else:
                    available = has_eligible_work(db, node, ['page'], min_priority=minimum)
            if not available and interest.document_count:
                available = has_document_work(db, node)
        return config_payload(node), replies, active, bool(available)


@router.websocket('/nodes/{node_id}/channel')
async def channel(socket: WebSocket, node_id: str):
    if SUBPROTOCOL not in socket.scope.get('subprotocols', []):
        await socket.close(code=1002)
        return
    try:
        connection = await run_in_threadpool(authenticated, node_id, socket.headers.get('authorization'))
    except HTTPException:
        await socket.close(code=1008)
        return
    notices = hub()
    receive = None
    interest, previous, sent, tracked = None, None, {}, set()
    clock = asyncio.get_running_loop().time
    hint_after = 0

    async def send(value):
        await asyncio.wait_for(socket.send_json(value), SEND_SECONDS)

    try:
        await socket.accept(subprotocol=SUBPROTOCOL)
        with notices.subscribe('compute') as wake, anyio.move_on_after(CHANNEL_SECONDS):
            while not notices.stop.is_set():
                wake.clear()
                if receive is not None and receive.done():
                    frame = receive.result()
                    receive = None
                    if frame['type'] == 'websocket.disconnect':
                        break
                    raw = frame.get('text')
                    if raw is None:
                        await socket.close(code=1003)
                        return
                    if len(raw.encode('utf-8')) > 4096:
                        await socket.close(code=1009)
                        return
                    try:
                        message = Pull.model_validate_json(raw)
                    except ValidationError:
                        await socket.close(code=1008)
                        return
                    try:
                        reply = await run_in_threadpool(assign, node_id, connection, message)
                    except (HTTPException, ProcessingError) as error:
                        code = error.detail['code'] if isinstance(error, HTTPException) else error.code
                        if code == 'NODE_AUTH_REQUIRED':
                            raise
                        reply = {'type': 'error', 'request_id': message.request_id, 'code': code}
                        await run_in_threadpool(withdraw, node_id, connection)
                    await send(reply)
                    interest = message if reply.get('leases') == [] else None
                if receive is None:
                    receive = asyncio.create_task(socket.receive())
                    receive.add_done_callback(lambda _: wake.set())
                config, replies, active, available = await run_in_threadpool(snapshot, node_id,
                    connection, tracked, interest if clock() >= hint_after else None)
                update = {}
                if config != previous:
                    update['config'] = config
                    previous = config
                if available:
                    update['available'] = True
                    interest = None
                    hint_after = clock() + PREFERENCE_SECONDS
                if update:
                    await send({'type': 'updates', **update})
                for reply in replies:
                    if sent.get(reply['lease_id']) != reply:
                        await send({'type': 'updates', 'leases': [reply]})
                sent = {reply['lease_id']: reply for reply in replies if reply['lease_id'] in active}
                tracked = active
                delay = hint_after - clock()
                with anyio.move_on_after(min(RECONCILE_SECONDS, delay) if interest and delay > 0 else RECONCILE_SECONDS):
                    await wake.wait()
    except HTTPException:
        await socket.close(code=1008)
    except (WebSocketDisconnect, TimeoutError):
        pass
    finally:
        # ASGI cancellation must still withdraw capacity and release pending receives.
        with anyio.CancelScope(shield=True):
            if receive is not None:
                receive.cancel()
                await asyncio.gather(receive, return_exceptions=True)
            try:
                await run_in_threadpool(withdraw, node_id, connection)
            except HTTPException:
                pass  # A newer connection or rotated credential owns the node now.
            if socket.application_state == WebSocketState.CONNECTED:
                try:
                    await asyncio.wait_for(socket.close(code=1001), SEND_SECONDS)
                except (WebSocketDisconnect, TimeoutError):
                    pass
