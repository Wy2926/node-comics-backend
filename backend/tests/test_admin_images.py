"""Task-scoped private previews, independent input/output expiry and byte streaming."""
from datetime import timedelta

import pytest

from conftest import login, upload, configure_system_limits
from test_asset_retention import finished
from app.assets import object_path
from app.db import session_factory
from app.models import Asset, Job, now


@pytest.fixture
def preview(client, png, monkeypatch):
    owner, _, job_id, request = finished(client, png, monkeypatch)
    return f'/v1/admin/monitor/tasks/{job_id}/images', login(client, 'admin'), owner, job_id, request


@pytest.mark.parametrize('suffix', ['', '/original', '/result'])
def test_images_require_admin_even_for_owner_and_conditional_read(client, preview, suffix):
    path, admin, owner, _, _ = preview
    assert client.get(path + suffix).status_code == 401
    for auth in (owner, login(client, 'other')):
        assert client.get(path + suffix, headers={**auth, 'If-None-Match': '"known"'}).status_code == 403
    assert client.get(path + suffix, headers=admin).status_code == 200


def test_preview_paths_stream_private_original_and_full_result(client, png, preview):
    path, admin, _, _, _ = preview
    response = client.get(path, headers=admin)
    data = response.json()
    assert response.headers['cache-control'] == 'private, no-store'
    assert data['original'] == path + '/original'
    assert data['result']['representation'] == 'full-image-v1'
    assert data['result']['artifact']['path'] == path + '/result'
    assert data['input_expires_at'] and data['result_expires_at']
    assert 'storage_key' not in response.text
    for image in (data['original'], data['result']['artifact']['path']):
        content = client.get(image, headers=admin)
        assert content.content == png and content.headers['content-type'] == 'image/png'
        assert content.headers['cache-control'] == 'private, no-store'
        assert content.headers['x-content-type-options'] == 'nosniff'
        assert client.get(image, headers={**admin, 'If-None-Match': content.headers['etag']}).status_code == 304


def test_original_expiry_uses_current_setting_before_cleanup_and_keeps_full_result(client, preview):
    path, admin, _, job_id, _ = preview
    with session_factory()() as db:
        job = db.get(Job, job_id)
        job.completed_at = now() - timedelta(hours=25)
        original_path = object_path(db.get(Asset, job.input_asset_id).storage_key)
        db.commit()
    data = client.get(path, headers=admin).json()
    assert data['original'] is None and data['original_error']
    assert data['result']['representation'] == 'full-image-v1'
    assert client.get(path + '/original', headers=admin).status_code == 410
    assert client.get(path + '/result', headers=admin).status_code == 200
    assert original_path.exists()
    configure_system_limits(image_input_retention_hours=26)
    assert client.get(path + '/original', headers=admin).status_code == 200


@pytest.mark.parametrize('representation', ['original', 'overlay-v1', 'overlay-tiles-v1'])
def test_preview_describes_compositing_without_starting_work(client, preview, representation):
    path, admin, _, job_id, _ = preview
    with session_factory()() as db:
        job = db.get(Job, job_id)
        if representation == 'original':
            job.status, job.output_asset_id = 'no_text', None
        else:
            output = db.get(Asset, job.output_asset_id)
            output.representation = representation
            output.mime = 'image/webp' if representation == 'overlay-v1' else 'application/vnd.nodelane.overlay-tiles'
            output.bbox = {'x': 3, 'y': 4, 'width': 10, 'height': 20} if representation == 'overlay-v1' else None
        db.commit()
    result = client.get(path, headers=admin).json()['result']
    assert result['representation'] == representation
    assert (result['width'], result['height']) == (320, 480)
    if representation == 'original':
        assert result['kind'] == 'no_text' and result['artifact'] is None
    else:
        assert result['composite'] == 'source-atop'
        assert result['artifact']['path'] == path + '/result'
        assert ('bbox' in result) == (representation == 'overlay-v1')


def test_deleted_requests_and_expired_results_cannot_be_previewed(client, preview):
    path, admin, owner, job_id, request = preview
    assert client.delete('/v1/translations/' + request, headers=owner).status_code == 200
    data = client.get(path, headers=admin).json()
    assert data['original'] is None and data['result'] is None
    for suffix in ('/original', '/result'):
        assert client.get(path + suffix, headers=admin).status_code == 410


def test_missing_bytes_fail_without_restarting_task_and_old_purges_stay_unavailable(client, preview):
    path, admin, _, job_id, _ = preview
    with session_factory()() as db:
        job = db.get(Job, job_id)
        source = db.get(Asset, job.input_asset_id)
        object_path(source.storage_key).unlink()
        output = db.get(Asset, job.output_asset_id)
        object_path(output.storage_key).write_bytes(b'broken')
    for suffix in ('/original', '/result'):
        assert client.get(path + suffix, headers=admin).status_code == 503
    with session_factory()() as db:
        job = db.get(Job, job_id)
        assert job.status == 'succeeded'
        db.get(Asset, job.input_asset_id).purged_at = now()
        db.commit()
    assert client.get(path, headers=admin).json()['original'] is None
    assert client.get(path + '/original', headers=admin).status_code == 410


def test_result_expiry_is_independent_and_unknown_or_document_tasks_are_rejected(client, preview):
    path, admin, _, job_id, _ = preview
    configure_system_limits(image_input_retention_hours=240)
    with session_factory()() as db:
        job = db.get(Job, job_id)
        job.completed_at = now() - timedelta(days=8)
        db.commit()
    data = client.get(path, headers=admin).json()
    assert data['original'] and data['result'] is None
    assert client.get(path + '/original', headers=admin).status_code == 200
    assert client.get(path + '/result', headers=admin).status_code == 410
    with session_factory()() as db:
        db.get(Job, job_id).workflow_kind = 'document'
        db.commit()
    assert client.get(path, headers=admin).status_code == 404
    assert client.get('/v1/admin/monitor/tasks/missing/images', headers=admin).status_code == 404


def test_task_pointers_cannot_expose_another_owners_original(client, png, preview):
    path, admin, _, job_id, _ = preview
    other_source = upload(client, login(client, 'other'), png)
    with session_factory()() as db:
        db.get(Job, job_id).input_asset_id = other_source
        db.commit()
    for suffix in ('', '/original', '/result'):
        assert client.get(path + suffix, headers=admin).status_code == 404
