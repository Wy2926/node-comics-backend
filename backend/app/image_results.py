"""Result descriptors and authorized streaming, shared by readers and administrators."""
import os

from fastapi.responses import Response, StreamingResponse
from starlette.background import BackgroundTask

from .assets import object_path
from .errors import problem


def result_json(job, source, output, path):
    description = job.result_description or {}
    representation = output.representation if output else 'original'
    result = {'kind': 'no_text' if job.status == 'no_text' else
        'partial' if 'unrecognized_regions' in (job.quality_flags or []) else 'translated',
        'representation': representation, 'input_sha256': job.source_sha256,
        'normalization_version': source.normalization_version if source else 1,
        'width': output.width if representation == 'full-image-v1' else description.get('width') or (source.width if source else None),
        'height': output.height if representation == 'full-image-v1' else description.get('height') or (source.height if source else None),
        'artifact': None, 'quality_flags': job.quality_flags or []}
    if output:
        result['artifact'] = {'sha256': output.sha256, 'byte_size': output.byte_size, 'mime': output.mime, 'path': path}
        if representation == 'overlay-v1':
            result.update(bbox=output.bbox, composite='source-atop')
        elif representation == 'overlay-tiles-v1':
            result['composite'] = 'source-atop'
    return result


def asset_response(db, asset, request):
    """Caller authorizes the asset before opening it, including conditional reads."""
    try:
        handle = object_path(asset.storage_key).open('rb')
        if os.fstat(handle.fileno()).st_size != asset.byte_size:
            handle.close()
            problem('RESULT_UNAVAILABLE', '图片文件完整性异常', 503)
    except OSError:
        problem('RESULT_UNAVAILABLE', '图片文件暂不可用', 503)
    headers = {'ETag': '"' + asset.sha256 + '"', 'Cache-Control': 'private, no-store',
        'Content-Length': str(asset.byte_size), 'X-Content-Type-Options': 'nosniff'}
    try:
        db.commit()
    except BaseException:
        handle.close()
        raise
    if request.headers.get('if-none-match') == headers['ETag']:
        handle.close()
        return Response(status_code=304, headers={key: value for key, value in headers.items() if key != 'Content-Length'})
    def chunks():
        try:
            while chunk := handle.read(256 * 1024):
                yield chunk
        finally:
            handle.close()
    return StreamingResponse(chunks(), media_type=asset.mime, headers=headers, background=BackgroundTask(handle.close))
