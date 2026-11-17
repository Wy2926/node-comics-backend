"""Real TLS/TCP loss and recovery on the dedicated Docker network."""
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
import faulthandler
import hashlib
from io import BytesIO
import json
import os
from pathlib import Path
import socket
import subprocess
from threading import Event, Thread
import time
from uuid import uuid4
import zipfile

import pytest

if os.environ.get('RUN_COMPUTE_NETWORK') != '1':
    pytest.skip('Run scripts/tests/rehearse_compute_network.py for isolated Docker/netem acceptance', allow_module_level=True)

import httpx
import numpy as np
from PIL import Image
from sqlalchemy import func, select
import uvicorn

from conftest import create, login_plus, upload
from test_compute_v3 import v3
from test_compute_v3_postgres import client
from test_postgres_concurrency import pg_scope
from test_compute_node_v3 import FixtureRuntime, Agent, Journal, Transport
from test_document_compute import documents
from test_document_quota_api import document_case
from app import classic
from app.adapters.llm import TextResponse
from app.db import session_factory
from app.models import Job, Ledger, TextCall
from app.queue_models import ComputeNode, ExecutionLease
from app.scheduler import claim_stage
from app.workers import run_control_stage

ORIGIN = 'https://wire:8443'
REPORT = Path('/report')


class Wire:
    def __init__(self):
        ip = socket.gethostbyname('wire')
        self.tc('qdisc', 'replace', 'dev', 'eth0', 'root', 'handle', '1:', 'prio')
        self.tc('filter', 'add', 'dev', 'eth0', 'parent', '1:', 'protocol', 'ip',
                'prio', '1', 'u32', 'match', 'ip', 'dst', ip + '/32', 'flowid', '1:3')
        self.tc('qdisc', 'add', 'dev', 'eth0', 'parent', '1:3', 'handle', '30:', 'netem')

    @staticmethod
    def tc(*args):
        return subprocess.check_output(['tc', *args], text=True)

    def set(self, *impairment):
        # Recreate: netem replace retains an omitted rate from the previous fault.
        self.tc('qdisc', 'del', 'dev', 'eth0', 'parent', '1:3')
        self.tc('qdisc', 'add', 'dev', 'eth0', 'parent', '1:3', 'handle', '30:',
                'netem', 'limit', '10000', *(impairment or ('delay', '0ms')))
        if not impairment:
            assert not self.stats()['options'].keys() & {'delay', 'rate', 'loss-random'}

    def stats(self):
        return next(row for row in json.loads(self.tc('-s', '-j', 'qdisc', 'show', 'dev', 'eth0'))
                    if row['kind'] == 'netem')

    def close(self):
        self.tc('qdisc', 'del', 'dev', 'eth0', 'root')


class ObservedTransport(Transport):
    def __init__(self, config):
        super().__init__(config)
        self.connections, self.disconnections, self.requests, self.http = [], [], [], []
        self.responses = []
        self.ranges = []
        def observed(request):
            path = request.url.path.rsplit('/', 1)[-1]
            self.http.append((time.monotonic(), request.method, path))
            if path == 'input' and request.headers.get('range'):
                self.ranges.append(request.headers['range'])
        self.control.event_hooks['request'] = [observed]
        self.control.event_hooks['response'] = [lambda response: self.responses.append(
            (time.monotonic(), response.request.url.path, response.status_code,
             response.headers.get('content-length')))]

    def listen(self, stop, updated):
        first = True
        def received(value):
            nonlocal first
            if first:
                self.connections.append(time.monotonic())
                first = False
            updated(value)
        try:
            return super().listen(stop, received)
        finally:
            self.disconnections.append(time.monotonic())

    def request_assignment(self, body):
        self.requests.append((time.monotonic(), dict(body)))
        return super().request_assignment(body)


class NetworkRun:
    def __init__(self, fixture, tmp_path, wire, monkeypatch, *, document_case=None):
        from app.main import app
        self.fixture, self.wire = fixture, wire
        self.document_case = document_case
        self.target, self.cut_at, self.interrupted = None, None, Event()
        self.input_chunks = 0
        self.unavailable = False
        self.supplier_calls = 0
        self.jobs = []
        self.peers = []
        self.worker = ThreadPoolExecutor(4)
        self.futures = []
        def supplier(*_):
            self.supplier_calls += 1
            return TextResponse('{"0":"Hello"}', {'input_tokens': 10, 'output_tokens': 2}, 'fixture')
        monkeypatch.setattr(classic, 'call_text', supplier)
        async def network_app(scope, receive, send):
            if self.unavailable:
                if scope['type'] == 'websocket':
                    await send({'type': 'websocket.close', 'code': 1013})
                else:
                    await send({'type': 'http.response.start', 'status': 503, 'headers': []})
                    await send({'type': 'http.response.body', 'body': b''})
                return
            async def observed(message):
                frame = json.loads(message['text']) if self.target == 'assignment' and message['type'] == 'websocket.send' else {}
                assignment = (frame.get('type') == 'assignment' and frame['leases']
                              and scope['path'].endswith(f"/{fixture['node']['node_id']}/channel"))
                response = message['type'] == 'http.response.start' and message['status'] == 200
                if scope['path'].endswith('/input') and message['type'] == 'http.response.body':
                    self.input_chunks += 1
                partial_input = (self.target == 'input' and scope['path'].endswith('/input')
                    and message['type'] == 'http.response.body' and self.input_chunks >= 3)
                if self.target and (assignment or
                        partial_input or response and self.target != 'input' and scope['path'].endswith('/' + self.target)):
                    self.target = None
                    wire.set('loss', '100%')
                    self.cut_at = time.monotonic()
                    self.interrupted.set()
                await send(message)
            await app(scope, receive, observed)
        # Abandoned blackholed HTTP streams may outlive the node's successful reconnect.
        self.server = uvicorn.Server(uvicorn.Config(network_app, host='0.0.0.0', port=18088,
            log_level='critical', lifespan='off', timeout_graceful_shutdown=5))
        self.server_thread = Thread(target=self.server.run, daemon=True)
        self.server_thread.start()
        config = {'node_id': fixture['node']['node_id'], 'node_token': fixture['node']['token'],
            'resource_id': 'test:cuda:0', 'control_url': ORIGIN, 'control_ca': '/report/cert.pem',
            'engine': {'gpu': 0}, 'local_pages': 4, 'max_leases': 4, 'document_require_sandbox': False}
        self.transport = ObservedTransport(config)
        self.journal = Journal(tmp_path / 'node')
        self.runtime = FixtureRuntime()
        self.agent = Agent(config, self.runtime, self.transport, self.journal)
        self.agent.documents._protocol = 2 if document_case else None
        deadline = time.monotonic() + 30
        while True:
            try:
                self.transport.control.get('/health/ready', timeout=1).raise_for_status()
                break
            except httpx.HTTPError:
                assert time.monotonic() < deadline, 'Docker TLS proxy did not become ready'
                time.sleep(.1)
        self.agent.register()
        self.until(lambda: bool(self.transport.connections), 5)

    def tick(self):
        for agent in (self.agent, *self.peers):
            agent.poll_control()
            agent.documents.poll_control()
            agent.reap()
        for future in self.futures:
            if future.done():
                future.result()
        self.futures = [f for f in self.futures if not f.done()]
        if len(self.futures) < 4:
            with session_factory()() as db:
                lease = claim_stage(db, 'control-text', ['text'])
                db.commit()
            if lease:
                self.futures.append(self.worker.submit(run_control_stage, lease.id))
            elif self.document_case:
                from app.documents.workflow import claim_control
                from app.documents.worker import execute
                with session_factory()() as db:
                    lease = claim_control(db, 'control-document-text')
                    db.commit()
                if lease:
                    self.futures.append(self.worker.submit(execute, lease))

    def until(self, predicate, timeout=30):
        start = time.monotonic()
        while time.monotonic() - start < timeout:
            self.tick()
            if predicate():
                return time.monotonic() - start
            time.sleep(.02)
        with (REPORT / 'timeout-stacks.txt').open('w') as output:
            faulthandler.dump_traceback(file=output)
        with session_factory()() as db:
            jobs = {job.id: job.status for job in db.scalars(select(Job).where(Job.id.in_(self.jobs)))}
        pages = [{'job_id': page.lease['job_id'], 'step': page.step,
                  'terminal': bool(page.terminal), 'stopped': page.stopped,
                  'running': bool(page.future and not page.future.done()),
                  'analysis_running': bool(page.analysis_future and not page.analysis_future.done())}
                 for page in self.agent.active_pages()]
        raise AssertionError(f'Network recovery timed out after {timeout}s: {jobs=}, {pages=}, '
                             f'http={Counter(path for _, _, path in self.transport.http)}, '
                             f'responses={self.transport.responses}')

    def hold(self, seconds):
        end = time.monotonic() + seconds
        self.until(lambda: time.monotonic() >= end, seconds + 5)

    def create(self, count):
        auth = login_plus(self.fixture['client'], 'net-' + uuid4().hex, pages=300)
        ids = []
        for index in range(count):
            stream = BytesIO()
            pixels = np.random.default_rng(len(self.jobs) + index).integers(0, 256, (384, 512, 3), dtype=np.uint8)
            Image.fromarray(pixels).save(stream, 'PNG')
            asset = upload(self.fixture['client'], auth, stream.getvalue())
            response = create(self.fixture['client'], auth, asset, key=uuid4().hex, language='en')
            assert response.status_code == 202, response.text
            ids.append(response.json()['id'])
        self.jobs.extend(ids)
        return ids

    def completed(self, ids):
        with session_factory()() as db:
            states = list(db.scalars(select(Job.status).where(Job.id.in_(ids))))
        assert not set(states) & {'failed', 'cancelled', 'outcome_unknown'}, (states, self.transport.responses)
        return len(states) == len(ids) and all(state == 'succeeded' for state in states)

    def finish(self, ids, timeout=120):
        return self.until(lambda: self.completed(ids) and not any(a.active_pages() for a in (self.agent, *self.peers)), timeout)

    def verify(self, *, recovered=False, pages=None):
        assert self.supplier_calls == len(self.jobs)
        assert self.runtime.rendered == (pages or len(self.jobs))
        if not recovered:
            assert self.runtime.analyzed == (pages or len(self.jobs))
        assert not self.journal.leases() and not self.journal.leases('document-lease:')
        with session_factory()() as db:
            assert db.scalar(select(func.count()).select_from(TextCall).where(TextCall.job_id.in_(self.jobs))) == len(self.jobs)
            for job_id in self.jobs:
                assert db.get(Job, job_id).settlement == 'settled'
                assert sorted(db.scalars(select(Ledger.kind).where(Ledger.job_id == job_id))) == ['reserve', 'settle']
            assert db.scalar(select(func.count()).select_from(ExecutionLease).where(
                ExecutionLease.completed_at.is_(None), ExecutionLease.node_id == self.fixture['node']['node_id'])) == 0

    def report(self, case, **metrics):
        metrics = {'case': case, 'pages': len(self.jobs), 'supplier_calls': self.supplier_calls,
            'analyzed': self.runtime.analyzed, 'rendered': self.runtime.rendered,
            'connections': len(self.transport.connections), 'range_retries': len(self.transport.ranges),
            'http_requests': dict(Counter(path for _, _, path in self.transport.http)), **metrics}
        (REPORT / (case + '.json')).write_text(json.dumps(metrics, indent=2), encoding='utf-8')
        print(json.dumps(metrics), flush=True)

    def close(self):
        self.wire.set()
        for agent in self.peers:
            agent.close()
            agent.transport.close()
            agent.journal.close()
        self.agent.close()
        self.worker.shutdown(wait=True)
        self.transport.close()
        self.journal.close()
        self.server.should_exit = True
        self.server_thread.join(10)
        assert not self.server_thread.is_alive()


@pytest.fixture
def network(v3, tmp_path, monkeypatch, request):
    case = request.getfixturevalue('documents') if getattr(request, 'param', None) == 'documents' else None
    wire = Wire()
    run = None
    try:
        run = NetworkRun(v3, tmp_path, wire, monkeypatch, document_case=case)
        yield run
    finally:
        try:
            if run:
                run.close()
        finally:
            wire.close()


def test_loss_jitter_bandwidth_and_recovered_throughput(network):
    started = time.monotonic()
    jobs = network.create(12)
    network.finish(jobs)
    baseline = time.monotonic() - started
    network.wire.set('delay', '100ms', '30ms', 'distribution', 'normal', 'loss', '3%', 'rate', '4mbit')
    jobs = network.create(8)
    slow = network.finish(jobs)
    impaired = network.wire.stats()
    assert impaired['drops'] > 0, impaired
    network.wire.set()
    started = time.monotonic()
    jobs = network.create(12)
    network.finish(jobs)
    recovered = time.monotonic() - started
    network.report('weak-network', baseline_seconds=baseline, impaired_seconds=slow,
        recovered_seconds=recovered, recovered_throughput_ratio=baseline / recovered, netem=impaired)
    assert recovered < max(8, baseline * 2), (baseline, recovered)
    network.verify()


@pytest.mark.parametrize('fault', ['blackhole', 'delay', 'lost-grant'])
def test_unresponsive_high_weight_node_does_not_strand_work(network, tmp_path, fault):
    from app.dispatcher import recover_once
    from test_compute_multinode import peer
    from test_node_scheduling import configure
    strong = network.fixture
    configure(strong, 100)
    if fault == 'lost-grant':
        # Exercise a short handoff policy against the normal 90s execution lease.
        with session_factory()() as db:
            node = db.get(ComputeNode, strong['node']['node_id'])
            node.desired_config = {**node.desired_config, 'request_seconds': 2, 'heartbeat_seconds': 1}
            node.config_version += 1
            node.applied_config_version = node.config_version
            db.commit()
    weak = peer(strong)
    config = {**network.agent.local, 'node_id': weak['node']['node_id'], 'node_token': weak['node']['token'],
              'resource_id': weak['registration']['resource_id'], 'control_url': 'https://healthy:8443'}
    healthy = Agent(config, FixtureRuntime(), ObservedTransport(config), Journal(tmp_path / 'healthy'))
    network.peers.append(healthy)
    healthy.register()
    def both_waiting():
        with session_factory()() as db:
            return all(db.get(ComputeNode, n['node']['node_id']).runtime_report.get('dispatch_slots', 0) > 0
                       for n in (strong, weak))
    network.until(both_waiting)
    if fault == 'lost-grant':
        network.until(lambda: network.agent.config['request_seconds'] == 2)
        network.target = 'assignment'
    else:
        impairment = ('loss', '100%') if fault == 'blackhole' else ('delay', '1500ms')
        network.wire.set(*impairment)
    started = time.monotonic()
    jobs = network.create(1)
    if fault == 'lost-grant':
        network.until(network.interrupted.is_set, 5)
        with session_factory()() as db:
            lease = db.scalars(select(ExecutionLease).where(ExecutionLease.job_id == jobs[0])).one()
            assert lease.node_id == strong['node']['node_id']
            assert 0 < (lease.expires_at - lease.started_at).total_seconds() < 13
        from app.models import now
        network.until(lambda: now() > lease.expires_at, 15)
        recover_once()
    network.finish(jobs, timeout=10)
    elapsed = time.monotonic() - started
    with session_factory()() as db:
        owners = list(db.scalars(select(ExecutionLease.node_id).where(ExecutionLease.job_id == jobs[0],
            ExecutionLease.resource_pool == 'image').order_by(ExecutionLease.generation)))
        expected = [strong['node']['node_id']] if fault == 'lost-grant' else []
        assert owners == expected + [config['node_id']]
        assert list(db.scalars(select(Ledger.kind).where(Ledger.job_id == jobs[0]).order_by(Ledger.kind))) == ['reserve', 'settle']
    assert network.supplier_calls == healthy.runtime.analyzed == healthy.runtime.rendered == 1
    assert network.runtime.analyzed == 0
    if fault != 'delay':
        assert network.wire.stats()['drops'] > 0
    if fault != 'lost-grant':
        assert elapsed < 8 and not network.transport.disconnections
    else:
        assert elapsed < 24, 'Handoff recovery must not wait for the normal execution lease'
    network.report('preferred-peer-' + fault, healthy_completion_seconds=elapsed, assigned_nodes=owners,
                   healthy_analyzed=healthy.runtime.analyzed, healthy_rendered=healthy.runtime.rendered)


@pytest.mark.parametrize('phase', ['assignment', 'input', 'analysis', 'result'])
def test_blackhole_replays_and_resumes_without_duplicate_work(network, phase):
    network.target = phase
    jobs = network.create(8)
    network.until(network.interrupted.is_set)
    network.until(lambda: bool(network.transport.disconnections), 25)
    detected = network.transport.disconnections[0] - network.cut_at
    assert detected < 15, detected
    network.hold(max(0, 30 - (time.monotonic() - network.cut_at)))
    dropped = network.wire.stats()
    assert dropped['drops'] > 0
    network.wire.set()
    restored = time.monotonic()
    network.until(lambda: any(at >= restored for at in network.transport.connections), 8)
    reconnected = network.transport.connections[-1] - restored
    finished = network.finish(jobs, 30)
    network.verify()
    if phase == 'assignment':
        requests = [body for _, body in network.transport.requests]
        assert Counter(body['request_id'] for body in requests).most_common(1)[0][1] >= 2
    if phase == 'input':
        assert network.transport.ranges, 'Interrupted input must resume from its verified prefix'
    network.report('blackhole-' + phase, detection_seconds=detected, reconnect_seconds=reconnected,
        resumed_completion_seconds=finished, netem=dropped)


def test_service_outage_does_not_leave_a_long_reconnect_backoff(network):
    network.unavailable = True
    network.transport.close_channel()
    network.until(lambda: len(network.transport.disconnections) >= 5, 45)
    attempts = len(network.transport.disconnections)
    network.unavailable = False
    restored = time.monotonic()
    network.until(lambda: any(at >= restored for at in network.transport.connections), 6)
    reconnected = network.transport.connections[-1] - restored
    jobs = network.create(8)
    network.finish(jobs)
    network.verify()
    network.report('service-outage', failed_connections=attempts, reconnect_seconds=reconnected)


def test_outage_past_lease_expiry_reuses_committed_analysis_and_text(network, monkeypatch):
    from app.config import settings
    from app.dispatcher import recover_once
    monkeypatch.setenv('CLUSTER_LEASE_SECONDS', '20')
    settings.cache_clear()
    network.target = 'analysis'
    jobs = network.create(4)
    network.until(network.interrupted.is_set)
    network.hold(25)
    recover_once()
    network.wire.set()
    restored = time.monotonic()
    network.finish(jobs, 45)
    network.verify(recovered=True)
    with session_factory()() as db:
        leases = list(db.scalars(select(ExecutionLease).where(
            ExecutionLease.job_id.in_(jobs), ExecutionLease.node_id == network.fixture['node']['node_id'])))
        assert any(lease.generation > 1 for lease in leases)
    network.report('lease-expiry', resumed_completion_seconds=time.monotonic() - restored,
        lease_seconds=20, blackout_seconds=25)


@pytest.mark.parametrize('network', ['documents'], indirect=True)
def test_document_completion_outage_preserves_workspace_and_single_settlement(network, monkeypatch):
    from app.documents import text
    from app.documents.models import Document
    from classic_node.document_workspace import DocumentWorkspace
    from test_document_quota_api import create as create_upload

    stream = BytesIO()
    with zipfile.ZipFile(stream, 'w') as archive:
        for index in range(4):
            image = BytesIO()
            Image.new('RGB', (160, 120), (index * 30, 120, 180)).save(image, 'PNG')
            archive.writestr(f'{index + 1}.png', image.getvalue())
    source = stream.getvalue()
    transfers = Counter()
    def download(ref, transfer, path, check):
        check()
        assert ref['sha256'] == hashlib.sha256(source).hexdigest()
        transfers['source'] += 1
        path.write_bytes(source)
    def upload_result(ref, transfer, path, check):
        check()
        assert hashlib.sha256(path.read_bytes()).hexdigest() == ref['sha256']
        with zipfile.ZipFile(path) as archive:
            assert len(archive.namelist()) == 4
        transfers['result'] += 1
    def render(original, source, *args, **kwargs):
        kwargs['check_cancelled']()
        network.runtime.rendered += 1
        data, metadata = source
        return {'output_bytes': data, 'result': {'output': {
            **{key: metadata[key] for key in ('width', 'height')}, 'mime': 'image/png',
            'byte_size': len(data), 'sha256': hashlib.sha256(data).hexdigest()}}}
    def supplier(*args, **kwargs):
        network.supplier_calls += 1
        return TextResponse(json.dumps({'translations': [
            {'id': f'p{index}:0', 'text': 'Hello'} for index in range(1, 5)]}),
            {'input_tokens': 40, 'output_tokens': 8}, 'fixture')
    monkeypatch.setattr(text, 'call_messages', supplier)
    monkeypatch.setattr(network.runtime, 'render', render)
    # R2 bytes are local fixtures; every control request still crosses TLS/netem.
    monkeypatch.setattr(network.agent.documents.objects, 'download_to_file', download)
    monkeypatch.setattr(network.agent.documents.objects, 'upload_file', upload_result)
    case = network.document_case
    request_id = create_upload(case.account, target_language='en',
        byte_size=len(source), sha256=hashlib.sha256(source).hexdigest())
    response = case.account.client.post('/v1/documents/' + request_id + '/complete-upload', headers=case.account.auth)
    assert response.status_code == 202, response.text
    with session_factory()() as db:
        job_id = db.scalar(select(Job.id).where(Job.idempotency_key == request_id, Job.owner_id == case.owner))
    network.jobs.append(job_id)
    network.target = 'complete'
    network.until(network.interrupted.is_set)
    inventory = DocumentWorkspace.inventory(network.agent.documents.state_dir)
    assert inventory == [{'job_id': job_id, 'workspace_generation': 1}]
    network.hold(30)
    assert DocumentWorkspace.inventory(network.agent.documents.state_dir) == inventory
    network.wire.set()
    restored = time.monotonic()
    network.until(lambda: any(at >= restored for at in network.transport.connections), 8)
    network.finish([job_id], 30)
    network.verify(pages=4)
    assert transfers == {'source': 1, 'result': 1}
    with session_factory()() as db:
        document = db.get(Document, job_id)
        assert document.workspace_generation == 1 and document.owner_node_id == case.v3['node']['node_id']
    network.agent.documents.sync()
    assert not DocumentWorkspace.inventory(network.agent.documents.state_dir)
    network.report('document-completion', pages=4, transfers=dict(transfers),
        reconnect_seconds=network.transport.connections[-1] - restored,
        resumed_completion_seconds=time.monotonic() - restored)
