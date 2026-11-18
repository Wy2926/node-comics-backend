import importlib.util
import json
from pathlib import Path
from unittest.mock import patch
from types import SimpleNamespace
import pytest

SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "database_backup.py"
spec = importlib.util.spec_from_file_location("database_backup", SCRIPT)
backup_tool = importlib.util.module_from_spec(spec)
spec.loader.exec_module(backup_tool)


def test_modified_backup_is_rejected_before_creating_restore_target(tmp_path):
    snapshot = tmp_path / "database.dump"
    snapshot.write_bytes(b"synthetic archive")
    (tmp_path / "manifest.json").write_text(json.dumps({"version": 1, "format": "postgresql-custom",
        "file": snapshot.name, "size": snapshot.stat().st_size, "sha256": backup_tool.checksum(snapshot)}))
    snapshot.write_bytes(b"modified archive!")
    with pytest.raises(backup_tool.BackupError, match="checksum"):
        backup_tool.restore(tmp_path, database_env='UNUSED_RESTORE_TEST_URL')


def test_backup_inventory_keeps_private_task_files_separate(client, png):
    from conftest import login, upload
    from app.db import engine
    upload(client, login(client, "alice"), png)
    upload(client, login(client, "bob"), png)
    with engine().connect() as connection:
        objects = backup_tool.object_references(connection.exec_driver_sql, {'assets'})
    assert len(objects) == 2 and all(item["kind"] == "temporary-input" and not item["required"] for item in objects)
    assert objects[0]["key"] != objects[1]["key"]


def test_inventory_excludes_purged_originals_and_uses_reservation_paths(pg_scope):
    from app.db import engine
    with engine().begin() as connection:
        execute = connection.exec_driver_sql
        execute('CREATE TEMP TABLE assets (storage_key TEXT, sha256 TEXT, mime TEXT, kind TEXT, deleted_at TEXT, purged_at TEXT)')
        execute('CREATE TEMP TABLE upload_reservations (id TEXT, expected_sha256 TEXT, mime TEXT, verified_info JSON, completed_at TEXT)')
        execute('INSERT INTO assets VALUES (%s, %s, %s, %s, NULL, %s)',
            ('inputs/finished/source', 'a' * 64, 'image/png', 'original', '2026-09-29'))
        execute('INSERT INTO assets VALUES (%s, %s, %s, %s, NULL, NULL)',
            ('results/aa/artifact', 'b' * 64, 'image/webp', 'classic'))
        execute('INSERT INTO upload_reservations VALUES (%s, %s, %s, %s, NULL)',
            ('reservation-id', 'c' * 64, 'image/png', json.dumps({'sha256': 'c' * 64})))
        execute('INSERT INTO upload_reservations VALUES (%s, %s, %s, %s, NULL)',
            ('no-bytes', 'd' * 64, 'image/png', 'null'))
        inventory = backup_tool.object_references(execute, {'assets', 'upload_reservations'})
    assert {row['key'] for row in inventory} == {'results/aa/artifact', 'inputs/reservation-id/source'}
    assert [row['key'] for row in inventory if row['required']] == ['results/aa/artifact']


def test_postgres_restore_refuses_product_database_before_connecting(tmp_path, monkeypatch):
    destination = tmp_path / "backup"
    destination.mkdir()
    snapshot = destination / "database.dump"
    snapshot.write_bytes(b"synthetic custom dump")
    (destination / "manifest.json").write_text(json.dumps({"version": 1, "format": "postgresql-custom",
        "file": snapshot.name, "size": snapshot.stat().st_size, "sha256": backup_tool.checksum(snapshot)}))
    monkeypatch.setenv("RESTORE_UNIT_TEST_URL", "postgresql://test:synthetic-password@127.0.0.1/nodecomics_production")
    with patch("psycopg.connect") as connect:
        with pytest.raises(backup_tool.BackupError, match="Isolated PostgreSQL"):
            backup_tool.restore(destination, database_env="RESTORE_UNIT_TEST_URL")
        connect.assert_not_called()


@pytest.mark.parametrize("wrong_port", [True, False])
def test_container_target_mismatch_is_rejected_before_database_creation(tmp_path, monkeypatch, wrong_port):
    destination = tmp_path / "backup"
    destination.mkdir()
    snapshot = destination / "database.dump"
    snapshot.write_bytes(b"synthetic custom dump")
    (destination / "manifest.json").write_text(json.dumps({"version": 1, "format": "postgresql-custom",
        "file": snapshot.name, "size": snapshot.stat().st_size, "sha256": backup_tool.checksum(snapshot)}))
    monkeypatch.setenv("RESTORE_UNIT_TEST_URL", "postgresql://test:synthetic-password@127.0.0.1:15432/nodecomics_restore_unit")
    inspected = SimpleNamespace(returncode=0, stdout=json.dumps({"5432/tcp": [
        {"HostIp": "127.0.0.1", "HostPort": "25432" if wrong_port else "15432"}]}).encode())
    with patch.object(backup_tool.subprocess, "run", return_value=inspected), patch("psycopg.connect") as connect, \
            patch.object(backup_tool, "_run_pg_command") as run:
        connect.return_value.__enter__.return_value.execute.return_value.fetchone.return_value = ("host-server:started",)
        run.side_effect = lambda *args, **kwargs: kwargs["output"].write(b"other-server:started\n")
        with pytest.raises(backup_tool.BackupError, match="does not match"):
            backup_tool.restore(destination, database_env="RESTORE_UNIT_TEST_URL", pg_container="isolated-test-postgres")
        if wrong_port:
            connect.assert_not_called()
            run.assert_not_called()
        else:
            assert connect.call_count == 1
            execute = connect.return_value.__enter__.return_value.execute
            assert execute.call_count == 1 and "pg_control_system" in execute.call_args.args[0]
            assert run.call_args.args[0] == "psql"
