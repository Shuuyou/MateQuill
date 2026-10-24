"""Authorized manual publication handoff; never posts to external platforms."""
from pathlib import Path
from copy import deepcopy

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import HTMLResponse, Response

from .publication_bundle import build_bundle

router = APIRouter(prefix='/api/runs', tags=['publication-handoff'])
HEADERS = {'Cache-Control': 'private, no-store', 'X-Content-Type-Options': 'nosniff'}


async def load_publication(run_id: str, expected_hash: str | None = None):
    from .resource_access import ensure_live_context
    from .tenancy import authorize_resource, require_context
    from .run_records import get_run
    from .parent_assets import read_parent_asset
    ensure_live_context()
    context = require_context()
    meta = authorize_resource('run', run_id, 'export')
    record = meta.get('body') if not context.is_legacy else get_run(run_id)
    if not record:
        raise HTTPException(404, '没有找到这篇内容')
    record = deepcopy(record)
    platform = (record.get('_poster_task') or {}).get('platform') or (record.get('strategy') or {}).get('platform')

    async def loader(asset_id):
        response = await read_parent_asset(meta, record, asset_id)
        raw = Path(response.path).read_bytes() if hasattr(response, 'path') else response.body
        return raw, response.media_type

    bundle = await build_bundle(record, run_id, meta.get('version', 1), platform, loader)
    # Authorization may be revoked, or a local edit committed, while files are read.
    ensure_live_context()
    current = authorize_resource('run', run_id, 'export')
    current_record = get_run(run_id) if context.is_legacy else current.get('body')
    if current.get('version', 1) != meta.get('version', 1) or current_record != record:
        raise HTTPException(409, '内容已经更新，请重新打开后下载')
    if expected_hash is not None and expected_hash != bundle.content_hash:
        raise HTTPException(409, '内容或配图已经更新，请重新打开后下载')
    return bundle


@router.get('/{run_id}/publication')
async def publication_manifest(run_id: str, expected_hash: str | None = Query(default=None, min_length=64, max_length=64)):
    bundle = await load_publication(run_id, expected_hash)
    from fastapi.responses import JSONResponse
    return JSONResponse(bundle.manifest(), headers=HEADERS)


@router.get('/{run_id}/publication/preview')
async def publication_preview(run_id: str, expected_hash: str = Query(min_length=64, max_length=64)):
    bundle = await load_publication(run_id, expected_hash)
    return HTMLResponse(bundle.document(), headers={**HEADERS, 'Content-Security-Policy':
                        "default-src 'none'; img-src data:; style-src 'unsafe-inline'; frame-ancestors 'self'"})


@router.get('/{run_id}/publication/package.zip')
async def publication_package(run_id: str, expected_hash: str = Query(min_length=64, max_length=64)):
    bundle = await load_publication(run_id, expected_hash)
    return Response(bundle.archive(), media_type='application/zip', headers={**HEADERS,
                    'Content-Disposition': 'attachment; filename="MateQuill-publication.zip"',
                    'X-Content-SHA256': bundle.content_hash})
