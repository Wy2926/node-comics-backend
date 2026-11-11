"""Container probes are bounded and never print driver exceptions or import the application."""
from contextlib import contextmanager
import os
from pathlib import Path
import subprocess
import sys

import pytest

from app import healthcheck


def test_probe_cli_hides_connection_errors(monkeypatch, capsys):
    def fail(*_):
        raise RuntimeError('postgresql://private:password@private-host/private-db')

    monkeypatch.setattr(healthcheck, 'ready', fail)
    monkeypatch.setattr(sys, 'argv', ['healthcheck', 'maintenance'])
    with pytest.raises(SystemExit) as stopped:
        healthcheck.main()
    assert stopped.value.code == 1
    captured = capsys.readouterr()
    assert captured.out == 'unavailable\n' and not captured.err


def test_probe_process_does_not_load_orm_or_application(tmp_path):
    env = {key: value for key, value in os.environ.items() if key.upper() in {
        'PATH', 'SYSTEMROOT', 'WINDIR', 'TEMP', 'TMP', 'USERPROFILE', 'HOME'}}
    env['PYTHONPATH'] = str(Path(__file__).resolve().parents[1])
    code = '''
import sys
from app.healthcheck import main
sys.argv = ['healthcheck', 'maintenance']
try:
    main()
except SystemExit as error:
    assert error.code == 1  # No DATABASE_URL: fail closed, never use a default database.
assert not {'sqlalchemy', 'pydantic', 'app.config', 'app.models', 'app.main'} & sys.modules.keys()
'''
    result = subprocess.run([sys.executable, '-c', code], cwd=tmp_path, env=env,
        capture_output=True, text=True, timeout=10)
    assert result.returncode == 0, result.stderr
    assert result.stdout == 'unavailable\n'


@pytest.mark.parametrize('heartbeat,redis_up,expected', [(True, True, True), (False, True, False), (True, False, False)])
def test_probe_dependency_gates_and_timeouts(monkeypatch, heartbeat, redis_up, expected):
    import psycopg
    from redis import Redis
    from unittest.mock import MagicMock

    monkeypatch.setenv('DATABASE_URL', 'postgresql+psycopg://test-host/test-db')
    monkeypatch.setenv('REDIS_URL', 'redis://test-redis/0')
    monkeypatch.setenv('CLUSTER_NODE_TIMEOUT_SECONDS', '30')
    connection, client = MagicMock(), MagicMock()
    connection.execute.return_value.fetchone.return_value = (1,) if heartbeat else None
    client.ping.return_value = redis_up

    @contextmanager
    def connect(url, **options):
        assert url == 'postgresql://test-host/test-db'
        assert options == {'connect_timeout': 2, 'autocommit': True}
        yield connection

    @contextmanager
    def redis(url, **options):
        assert url == 'redis://test-redis/0'
        assert options['socket_connect_timeout'] == options['socket_timeout'] == 1
        yield client

    monkeypatch.setattr(psycopg, 'connect', connect)
    monkeypatch.setattr(Redis, 'from_url', redis)
    assert healthcheck.ready('maintenance', 123) is expected
    assert connection.execute.call_args.args[1][1] == healthcheck.instance_id(123)
    assert client.ping.call_count == int(heartbeat)
