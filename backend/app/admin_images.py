"""On-demand, administrator-only task image previews."""
from typing import Literal

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse, Response
from sqlalchemy.orm import Session

from .auth import admin
from .db import get_db
from .entitlements import iso
from .errors import problem
from .image_results import asset_response, result_json
from .models import Asset, Job, now
from .result_retention import input_expires_at, result_expired, result_expires_at

router = APIRouter(prefix='/v1/admin/monitor/tasks', dependencies=[Depends(admin)])


def task_images(db, job_id):
    job = db.get(Job, job_id)
    if not job or job.workflow_kind != 'image':
        problem('NOT_FOUND', '图片任务不存在', 404)
    source = db.get(Asset, job.input_asset_id) if job.input_asset_id else None
    output = db.get(Asset, job.output_asset_id) if job.output_asset_id else None
    # Task pointers never grant access to another owner's files.
    if ((source and source.owner_id != job.owner_id) or
            (output and (output.owner_id != job.owner_id or output.parent_id != job.input_asset_id))):
        problem('NOT_FOUND', '图片不存在', 404)
    deadline = input_expires_at(db, job)
    source_error = ('原图尚未上传' if not source else '原图已删除或过期' if
        source.deleted_at or source.purged_at or (deadline and deadline <= now()) or
        (source.expires_at and source.expires_at <= now()) else None)
    result_error = ('尚无译图' if job.status not in {'succeeded', 'no_text'} else
        '译图已删除或过期' if result_expired(db, job) or job.discard_output or
        (source and source.deleted_at) or (output and (output.deleted_at or output.purged_at or
            (output.expires_at and output.expires_at <= now()))) else
        '译图文件不可用' if job.status == 'succeeded' and not output else None)
    path = f'/v1/admin/monitor/tasks/{job.id}/images'
    data = {'original': path + '/original' if not source_error else None,
        'original_error': source_error, 'input_expires_at': iso(deadline),
        'result': result_json(job, source, output, path + '/result') if not result_error else None,
        'result_error': result_error, 'result_expires_at': iso(result_expires_at(db, job))}
    return data, source, output


@router.get('/{job_id}/images')
def images(job_id: str, db: Session = Depends(get_db)):
    data, _, _ = task_images(db, job_id)
    return JSONResponse(data, headers={'Cache-Control': 'private, no-store'})


@router.get('/{job_id}/images/{kind}', response_class=Response)
def image(job_id: str, kind: Literal['original', 'result'], request: Request, db: Session = Depends(get_db)):
    data, source, output = task_images(db, job_id)
    asset = source if kind == 'original' else output
    error = data['original_error' if kind == 'original' else 'result_error']
    if error:
        problem('IMAGE_UNAVAILABLE', error, 410 if asset else 409)
    if not asset:
        problem('RESULT_NOT_AVAILABLE', '无字结果使用原图', 409)
    return asset_response(db, asset, request)
