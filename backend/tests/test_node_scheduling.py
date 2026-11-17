"""Weighted placement through real SQL and durable claim receipts."""
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta

import pytest
from sqlalchemy import select

from app import compute_channel
from app.db import session_factory
from app.models import Job, now
from app.queue_models import ComputeNode, ExecutionLease
from compute_pull import bound_pull
from test_compute_claims import synchronized_preparation
from test_compute_multinode import peer
from test_compute_v3 import v3, analyze, claim
from test_document_compute import documents, create, claim as document_claim, complete, other_node
from test_document_quota_api import document_case


def configure(node, weight, *, slots=4, documents=1):
    with session_factory()() as db:
        row = db.get(ComputeNode, node['node']['node_id'])
        row.capacity = slots
        row.desired_config = {**row.desired_config, 'performance_weight': weight,
            'execution_slots': slots, 'document_slots': documents}
        db.commit()


@pytest.mark.parametrize('order', ['weak-first', 'strong-first', 'parallel'])
def test_weight_two_distributes_six_pages_four_to_two(v3, monkeypatch, order):
    strong = peer(v3)
    configure(strong, 2)
    pulls = [bound_pull(node) for node in (v3, strong)]
    for pull in pulls:
        assert pull(4) == []
    jobs = set(v3['create'](6))
    if order == 'parallel':
        synchronized_preparation(monkeypatch, 2)
        with ThreadPoolExecutor(2) as pool:
            batches = list(pool.map(lambda pull: pull(4), pulls))
    elif order == 'strong-first':
        strong_batch = pulls[1](4)
        batches = [pulls[0](4), strong_batch]
    else:
        batches = [pull(4) for pull in pulls]
    assert list(map(len, batches)) == [2, 4]
    assert {item['job_id'] for batch in batches for item in batch} == jobs
    with session_factory()() as db:
        leases = db.scalars(select(ExecutionLease)).all()
        assert len(leases) == len({lease.stage_id for lease in leases}) == 6


def test_equal_weights_spread_small_backlog_instead_of_filling_first_node(v3):
    other = peer(v3)
    configure(other, 1)
    pull = bound_pull(other)
    assert pull(4) == []
    v3['create'](4)
    assert len(claim(v3, 4)) == 2
    assert len(pull(4)) == 2


@pytest.mark.parametrize('mutation', ['offline', 'disabled', 'unready', 'language', 'font',
    'overlay', 'config_error', 'unsynced', 'full', 'expired'])
def test_unavailable_strong_peer_does_not_block_weak_node(v3, mutation):
    strong = peer(v3)
    configure(strong, 100, slots=1)
    pull = bound_pull(strong)
    if mutation in {'full', 'expired'}:
        v3['create']()
        active = pull()[0]
    assert pull() == []
    jobs = v3['create']()
    with session_factory()() as db:
        node = db.get(ComputeNode, strong['node']['node_id'])
        if mutation == 'offline':
            node.heartbeat_at = now() - timedelta(hours=1)
        elif mutation == 'disabled':
            node.enabled = False
        elif mutation == 'unready':
            node.capabilities = []
            node.runtime_report = {**node.runtime_report, 'ready': False}
        elif mutation == 'language':
            node.supported_languages = ['ja']
        elif mutation == 'font':
            node.runtime_report = {**node.runtime_report, 'supported_font_ids': []}
        elif mutation == 'overlay':
            job = db.get(Job, jobs[0])
            job.config = {**job.config, 'result_format': 'overlay-tiles-v1'}
            weak = db.get(ComputeNode, v3['node']['node_id'])
            weak.runtime_report = {**weak.runtime_report, 'overlay_tiles': True}
        elif mutation == 'config_error':
            node.config_error = 'CONFIG_FAILED'
        elif mutation == 'unsynced':
            node.applied_config_version = 0
        elif mutation == 'expired':
            db.get(ExecutionLease, active['lease_id']).expires_at = now() - timedelta(seconds=1)
        db.commit()
    assert len(claim(v3, 4)) == 1


def test_weight_changes_after_preparation_are_rechecked_under_lock(v3, monkeypatch):
    strong = peer(v3)
    pull = bound_pull(strong)
    assert pull() == []
    v3['create']()
    original = compute_channel.prepare_claim_candidates
    def prepare(*args, **kwargs):
        candidates = original(*args, **kwargs)
        configure(strong, 10)
        return candidates
    monkeypatch.setattr(compute_channel, 'prepare_claim_candidates', prepare)
    assert claim(v3) == []
    assert len(pull()) == 1


def test_completed_work_releases_weighted_load(v3):
    strong = peer(v3)
    configure(strong, 2, slots=1)
    pull = bound_pull(strong)
    v3['create'](2)
    first = pull()[0]
    assert len(claim(v3)) == 1
    analyze(strong, first, empty=True)
    assert pull() == []
    v3['create']()
    assert claim(v3) == []
    assert len(pull()) == 1


def test_document_placement_uses_weight_but_preserves_owned_workspace(documents):
    strong = other_node(documents)
    configure(strong.v3, 4)
    pull = bound_pull(strong.v3)
    assert pull(0, document_count=1) == []
    job_id = create(documents, prepared=False)
    assert document_claim(documents) == []
    prepare = pull(0, document_count=1)[0]
    assert prepare['job_id'] == job_id and prepare['action'] == 'doc_prepare'
    assert complete(strong, prepare).status_code == 200
    configure(documents.v3, 100)
    assert document_claim(documents) == []
    ocr = document_claim(strong)[0]
    assert ocr['job_id'] == job_id and ocr['action'] == 'doc_ocr'


@pytest.mark.parametrize('mutation', ['disabled-documents', 'ordinary-ready', 'baseline-full'])
def test_document_host_selection_respects_peer_admission(documents, mutation):
    strong = other_node(documents)
    configure(strong.v3, 10, documents=0 if mutation == 'disabled-documents' else 1)
    pull = bound_pull(strong.v3)
    assert pull(0, document_count=1) == []
    if mutation == 'ordinary-ready':
        # Only the strong peer can process this image language.
        from conftest import configure_system_limits
        configure_system_limits(free_daily_pages=20)
        documents.v3['create']()
        with session_factory()() as db:
            node = db.get(ComputeNode, documents.v3['node']['node_id'])
            node.supported_languages = ['zh-Hans']
            db.commit()
    elif mutation == 'baseline-full':
        create(documents, prepared=False)
        assert pull(0, document_count=1)
        assert pull(0, document_count=1) == []
    job_id = create(documents, prepared=False)
    leases = document_claim(documents)
    assert len(leases) == 1 and leases[0]['job_id'] == job_id
