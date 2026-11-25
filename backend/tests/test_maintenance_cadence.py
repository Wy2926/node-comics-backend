"""Retention scans must not delay frequent recovery or retry a failed pass only after a minute."""
from types import SimpleNamespace
from unittest.mock import Mock

import pytest


@pytest.mark.parametrize('fail_first_cleanup', [False, True])
def test_cleanup_cadence_preserves_recovery_and_retries(monkeypatch, fail_first_cleanup):
    from app import dispatcher, execution_retention, quota_campaigns
    import threading

    clock = [0]
    calls = {name: [] for name in ('recovery', 'cleanup', 'retention', 'checkpoints', 'progress', 'failure')}

    class Stop:
        def is_set(self):
            return clock[0] > 21604

        def wait(self, seconds):
            clock[0] = 21600 if clock[0] == 122 else clock[0] + seconds

    class Session:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        def commit(self):
            pass

    def cleanup(db):
        calls['cleanup'].append(clock[0])
        if fail_first_cleanup and len(calls['cleanup']) == 1:
            raise OSError('retry this batch')

    monkeypatch.setattr(dispatcher, 'Event', Stop)
    monkeypatch.setattr(dispatcher.signal, 'signal', lambda *_: None)
    monkeypatch.setattr(threading, 'Thread', Mock())
    monkeypatch.setattr(dispatcher.time, 'monotonic', lambda: clock[0])
    monkeypatch.setattr(dispatcher, 'check_runtime', lambda: None)
    monkeypatch.setattr(dispatcher, 'settings', lambda: SimpleNamespace(
        dispatch_interval_seconds=2, cluster_node_timeout_seconds=120))
    monkeypatch.setattr(dispatcher, 'session_factory', lambda: Session)
    monkeypatch.setattr(dispatcher, 'recover_once', lambda: calls['recovery'].append(clock[0]))
    monkeypatch.setattr(dispatcher, 'cleanup', cleanup)
    monkeypatch.setattr(dispatcher, 'probe_oidc', lambda: None)
    monkeypatch.setattr(dispatcher, 'report_progress', lambda *_: calls['progress'].append(clock[0]))
    monkeypatch.setattr(dispatcher, 'report_failure', lambda *_: calls['failure'].append(clock[0]))
    monkeypatch.setattr(execution_retention, 'cleanup_execution_data', lambda _: calls['retention'].append(clock[0]))
    def compact(db, *, at):
        calls['checkpoints'].append((clock[0], at))
        return {'checkpoints': 20 if len(calls['checkpoints']) == 1 else 0}
    monkeypatch.setattr(execution_retention, 'compact_checkpoints', compact)
    monkeypatch.setattr(quota_campaigns, 'backfill_campaigns', lambda: None)
    dispatcher.main()

    first = 2 if fail_first_cleanup else 0
    assert calls['recovery'] == [*range(0, 123, 2), 21600, 21602, 21604]
    assert calls['cleanup'] == ([0, 2, 62, 122] if fail_first_cleanup else [0, 60, 120]) + [21600]
    assert calls['failure'] == ([0] if fail_first_cleanup else [])
    assert calls['progress'] == [*range(first, 123, 2), 21600, 21602, 21604]
    assert calls['retention'] == [*range(first, 123, 10), 21600]
    assert [t for t, _ in calls['checkpoints']] == [first, first + 2, first + 21600]
    assert calls['checkpoints'][0][1] == calls['checkpoints'][1][1]
