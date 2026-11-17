"""Duplex capacity offers, durable assignments and scoped execution updates."""
import asyncio
from datetime import timedelta
import hmac
from typing import Annotated, Literal

import anyio
from fastapi import APIRouter, HTTPException, WebSocket, WebSocketDisconnect
from pydantic import Field, TypeAdapter, ValidationError, model_validator
from sqlalchemy import select
from starlette.concurrency import run_in_threadpool
from starlette.websockets import WebSocketState

from .compute_v3 import UpdateLease, config_payload, lease_payload, lease_updates, stamp
from .db import session_factory
from .errors import ProcessingError, problem
from .models import now, uid
from .node_auth import node_auth
from .notifications import hub
from .providers import digest
from .queue_models import ComputeClaim, ComputeNode, ExecutionLease, JobStage
from .request_models import RequestBody
from .scheduler import claim_batch, lock_scheduler, prepare_claim_candidates

router = APIRouter(prefix='/internal/compute/v3')
RECONCILE_SECONDS = 5
SEND_SECONDS = 10
CHANNEL_SECONDS = 300  # Let reloaded proxies drain through normal node reconnection.


class Ready(RequestBody):
    type: Literal['ready']
    request_id: str = Field(min_length=1, max_length=64, pattern=r'^[A-Za-z0-9_-]+$')
    config_version: int = Field(ge=1, strict=True)
    count: int = Field(ge=0, le=4, strict=True)
    document_count: int = Field(ge=0, le=1, strict=True)

    @model_validator(mode='after')
    def capacity(self):
        if not self.count and not self.document_count:
            raise ValueError('Offer at least one execution slot')
        return self


class Ack(RequestBody):
    type: Literal['ack']
    request_id: str = Field(min_length=1, max_length=64, pattern=r'^[A-Za-z0-9_-]+$')


MESSAGE = TypeAdapter(Annotated[Ready | Ack, Field(discriminator='type')])


def authenticated(node_id, authorization, identity):
    with session_factory()() as db:
        lock_scheduler(db)
        if node_auth(authorization, identity, db) != node_id:
            problem('NODE_SCOPE_MISMATCH', '节点身份不匹配', 403)
        node = db.get(ComputeNode, node_id)
        if node.runtime_report.get('protocol_version') != 3:
            problem('PROTOCOL_MISMATCH', '请先注册整页协议', 409)
        connection = uid()
        node.runtime_report = {**node.runtime_report, 'channel_id': connection,
            'dispatch_slots': 0, 'dispatch_documents': 0}
        db.commit()
        return node.credential_hash, connection


def current_node(db, identity, credential, connection):
    node = db.get(ComputeNode, identity, populate_existing=True)
    if not node or not hmac.compare_digest(node.credential_hash or '', credential):
        problem('NODE_AUTH_REQUIRED', '计算节点认证已失效', 401)
    if node.runtime_report.get('channel_id') != connection:
        problem('NODE_AUTH_REQUIRED', '计算节点连接已替换', 401)
    return node


def offer(identity, credential, connection, body=None):
    with session_factory()() as db:
        lock_scheduler(db)
        node = current_node(db, identity, credential, connection)
        node.runtime_report = {**node.runtime_report, 'dispatch_slots': body.count if body else 0,
            'dispatch_documents': body.document_count if body else 0,
            'dispatch_until': stamp(now() + timedelta(seconds=15))}
        db.commit()


def assign(identity, credential, connection, body):
    """An empty offer remains open; only actual grants create replay receipts."""
    from .documents.compute import claimable_rows, payload, start_document_lease
    with session_factory()() as db:
        saved = db.get(ComputeClaim, (identity, body.request_id))
        prepared = prepare_claim_candidates(db, identity, ['page'], limit=body.count) if body.count and not saved else None
        lock_scheduler(db)
        node = current_node(db, identity, credential, connection)
        saved = db.get(ComputeClaim, (identity, body.request_id), populate_existing=True)
        request_hash = digest({'channel_version': 1, **body.model_dump()})
        if saved:
            if saved.request_hash != request_hash:
                problem('CLAIM_CONFLICT', '同一容量编号不能修改内容', 409)
            leases = [db.get(ExecutionLease, key) for key in saved.lease_ids]
        else:
            if body.config_version != node.config_version:
                problem('NODE_CONFIG_CONFLICT', '领取前需要同步配置', 409)
            node.runtime_report = {**node.runtime_report,
                'dispatch_until': stamp(now() + timedelta(seconds=15))}
            leases = claim_batch(db, identity, ['page'], limit=body.count,
                config_version=body.config_version, prepared=prepared) if prepared else []
            if not leases and body.document_count and node.runtime_report.get('document_protocol') == 2:
                rows = claimable_rows(db, node)
                if rows:
                    leases = [start_document_lease(db, node, rows[0])]
            if leases:
                db.add(ComputeClaim(node_id=identity, request_id=body.request_id,
                    request_hash=request_hash, lease_ids=[lease.id for lease in leases]))
        if leases:
            node.runtime_report = {**node.runtime_report, 'dispatch_slots': 0, 'dispatch_documents': 0}
        # Commit before network I/O: replay recovers a lost response or ACK.
        db.commit()
        if not leases:
            return None
        return {'type': 'assignment', 'request_id': body.request_id, 'server_time': stamp(now()),
            'config': config_payload(node),
            'leases': [{**(payload if lease.limits.get('document_protocol') == 2 else lease_payload)(db, lease),
                        'workflow_kind': 'document' if lease.limits.get('document_protocol') == 2 else 'image'}
                       for lease in leases]}


def snapshot(identity, credential, connection, tracked):
    from .documents.compute import NODE_STAGES, live, receipt
    with session_factory()() as db:
        node = current_node(db, identity, credential, connection)
        active = set(db.scalars(select(ExecutionLease.id).where(
            ExecutionLease.node_id == identity, ExecutionLease.completed_at.is_(None))))
        rows = db.execute(select(ExecutionLease, JobStage.name).join(JobStage).where(
            ExecutionLease.node_id == identity, ExecutionLease.id.in_(active | tracked))).all()
        replies = lease_updates(db, identity, [UpdateLease(lease_id=lease.id, lease_token=lease.token)
            for lease, stage in rows if stage == 'page'])
        for lease, stage in rows:
            if stage not in NODE_STAGES:
                continue
            if lease.completed_at:
                replies.append(receipt(lease))
            else:
                try:
                    live(db, lease)
                except ProcessingError:
                    replies.append({'lease_id': lease.id, 'status': 'stop', 'code': 'LEASE_STOPPED'})
        return config_payload(node), replies, active


@router.websocket('/nodes/{node_id}/channel')
async def channel(socket: WebSocket, node_id: str):
    try:
        credential, connection = await run_in_threadpool(authenticated, node_id,
            socket.headers.get('authorization'), socket.headers.get('x-node-id'))
    except HTTPException:
        await socket.close(code=1008)
        return
    await socket.accept(subprotocol='compute.v3' if 'compute.v3' in socket.scope.get('subprotocols', []) else None)
    notices = hub()
    receive = asyncio.create_task(socket.receive())
    wake_task = None
    pending, awaiting_ack, previous, sent, tracked = None, None, None, {}, set()

    async def send(value):
        await asyncio.wait_for(socket.send_json(value), SEND_SECONDS)

    try:
        with notices.subscribe('compute') as wake, anyio.move_on_after(CHANNEL_SECONDS):
            while not notices.stop.is_set():
                wake.clear()
                if receive.done():
                    frame = receive.result()
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
                        message = MESSAGE.validate_json(raw)
                    except ValidationError:
                        await socket.close(code=1008)
                        return
                    if isinstance(message, Ack):
                        if message.request_id != awaiting_ack:
                            await socket.close(code=1008)
                            return
                        awaiting_ack = None
                    elif (awaiting_ack and message.request_id != awaiting_ack) or (pending and pending != message):
                        await socket.close(code=1008)
                        return
                    else:
                        awaiting_ack = None
                        pending = message
                        await run_in_threadpool(offer, node_id, credential, connection, message)
                    receive = asyncio.create_task(socket.receive())
                config, replies, active = await run_in_threadpool(snapshot, node_id, credential, connection, tracked)
                if config != previous:
                    await send({'type': 'updates', 'config': config})
                    previous = config
                for reply in replies:
                    if sent.get(reply['lease_id']) != reply:
                        await send({'type': 'updates', 'leases': [reply]})
                sent = {reply['lease_id']: reply for reply in replies if reply['lease_id'] in active}
                tracked = active
                if pending and not awaiting_ack:
                    try:
                        assignment = await run_in_threadpool(assign, node_id, credential, connection, pending)
                    except (HTTPException, ProcessingError) as error:
                        code = error.detail['code'] if isinstance(error, HTTPException) else error.code
                        if code == 'NODE_AUTH_REQUIRED':
                            raise
                        await send({'type': 'error', 'request_id': pending.request_id, 'code': code})
                        await run_in_threadpool(offer, node_id, credential, connection)
                        pending = None
                    else:
                        if assignment:
                            awaiting_ack, pending = pending.request_id, None
                            await send(assignment)
                wake_task = asyncio.create_task(wake.wait())
                await asyncio.wait((receive, wake_task), timeout=RECONCILE_SECONDS, return_when=asyncio.FIRST_COMPLETED)
                wake_task.cancel()
                # A second cancellation during join can lose the enclosing scope's identity.
                with anyio.CancelScope(shield=True):
                    await asyncio.gather(wake_task, return_exceptions=True)
    except HTTPException:
        await socket.close(code=1008)
    except (WebSocketDisconnect, TimeoutError):
        pass
    finally:
        # ASGI cancellation must still withdraw capacity and release pending receives.
        with anyio.CancelScope(shield=True):
            receive.cancel()
            if wake_task:
                wake_task.cancel()
            await asyncio.gather(receive, *([wake_task] if wake_task else []), return_exceptions=True)
            try:
                await run_in_threadpool(offer, node_id, credential, connection)
            except HTTPException:
                pass  # A newer connection or rotated credential owns the node now.
            if socket.application_state == WebSocketState.CONNECTED:
                try:
                    await asyncio.wait_for(socket.close(code=1001), SEND_SECONDS)
                except (WebSocketDisconnect, TimeoutError):
                    pass
