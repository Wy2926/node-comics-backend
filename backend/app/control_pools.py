"""Shared control capacity: initialized once, then owned by administrators."""
from sqlalchemy import update
from .config import settings
from .models import now
from .queue_models import ComputeNode
from .scheduler import lock_scheduler

POOL_LIMITS = {'text': 100, 'validate_upload': 32}
DOCUMENT_POOL_LIMITS = {'doc_text': 4}
POOL_LABELS = {'text': '文本翻译', 'validate_upload': '上传校验'}


def initialize_pools(db):
    cfg = settings()
    defaults = {'text': cfg.cluster_text_slots,
                'validate_upload': cfg.cluster_upload_slots}
    lock_scheduler(db)
    for stage, slots in defaults.items():
        node_id = 'control-' + stage
        if db.get(ComputeNode, node_id) is None:
            db.add(ComputeNode(id=node_id, name=POOL_LABELS[stage], resource_id=node_id,
                capabilities=[stage], capacity=slots, engine_version='control', device='network',
                heartbeat_at=None, desired_config={'execution_slots': slots}, applied_config_version=1))
    db.commit()


def report_pools(db, node_ids=('control-text', 'control-validate_upload')):
    """Heartbeat even disabled/full pools; capacity is never overwritten by workers."""
    if db.execute(update(ComputeNode).where(ComputeNode.id.in_(node_ids)).values(heartbeat_at=now())).rowcount != len(node_ids):
        raise RuntimeError('Control pool is not initialized')
    db.commit()
