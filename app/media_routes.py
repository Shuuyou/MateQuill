"""Multipart intake router; bounded before multipart parsing and private previews."""
from __future__ import annotations

import asyncio
from functools import lru_cache
import time
from typing import Literal

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import FileResponse
from starlette.datastructures import UploadFile
from starlette.formparsers import MultiPartException, MultiPartParser
from pydantic import BaseModel, Field

from .config import ROOT, settings
from .media_intake import MAX_UPLOAD_BYTES, MediaInputError, MediaStore, process_media

router = APIRouter()
_gate = asyncio.Semaphore(2)
_requests: dict[str, list[float]] = {}


class MediaObservations(BaseModel):
    caption: str = ''
    visible_text: list[str] = Field(default_factory=list)
    product_candidates: list[str] = Field(default_factory=list)
    scene_hints: list[str] = Field(default_factory=list)


class MediaRecognitionError(BaseModel):
    code: str
    message: str


class MediaIntakeResult(BaseModel):
    id: str
    kind: Literal['photo', 'audio']
    status: Literal['ready', 'needs_configuration', 'retryable']
    transcript: str
    observations: MediaObservations
    preview_url: str
    questions: list[str]
    error: MediaRecognitionError | None = None


@lru_cache(maxsize=64)
def _scoped_media_store(scope: str) -> MediaStore:
    return MediaStore(ROOT / 'runtime' / 'media-intake' / scope)


def media_store() -> MediaStore:
    from .tenancy import get_context
    context = get_context()
    return _scoped_media_store(context.tenant_id if context and not context.is_legacy else 'legacy')


async def _bounded_form(request: Request):
    if not request.headers.get('content-type', '').lower().startswith('multipart/form-data;'):
        raise HTTPException(415, '请上传照片或录音文件')
    limit = MAX_UPLOAD_BYTES + 64 * 1024
    raw = bytearray()
    async for chunk in request.stream():
        raw.extend(chunk)
        if len(raw) > limit:
            raise HTTPException(413, '文件超过 12 MB，请缩小后上传')
    async def stream():
        yield bytes(raw)
        yield b''
    try:
        return await MultiPartParser(request.headers, stream(), max_files=1, max_fields=2).parse()
    except (MultiPartException, ValueError) as exc:
        raise HTTPException(400, '上传未完成，请重新选择文件') from exc


@router.post('/api/media/intake', response_model=MediaIntakeResult, response_model_exclude_none=True,
             openapi_extra={'requestBody': {'required': True, 'content': {'multipart/form-data': {
                 'schema': {'type': 'object', 'required': ['file', 'kind'], 'properties': {
                     'file': {'type': 'string', 'format': 'binary'},
                     'kind': {'type': 'string', 'enum': ['photo', 'audio']}}}}}}})
async def media_intake(request: Request):
    from .resource_access import request_budget_key
    identity = request_budget_key(request)
    now = time.monotonic()
    for key in list(_requests):
        _requests[key] = [stamp for stamp in _requests[key] if stamp > now - 60]
        if not _requests[key]:
            del _requests[key]
    history = _requests.setdefault(identity, [])
    if len(history) >= 12:
        raise HTTPException(429, '刚才处理的素材比较多，请稍后再试')
    history.append(now)
    form = await _bounded_form(request)
    try:
        file = form.get('file')
        kind = form.get('kind')
        if not isinstance(file, UploadFile) or kind not in {'photo', 'audio'}:
            raise HTTPException(422, '请选择照片或录音后再上传')
        raw = await file.read(MAX_UPLOAD_BYTES + 1)
        if len(raw) > MAX_UPLOAD_BYTES:
            raise HTTPException(413, '文件超过 12 MB，请缩小后上传')
        async with _gate:
            from .tenancy import get_context
            from .tenancy.auth import guest_state
            context = get_context()
            if context and not context.is_legacy and guest_state(context.user_id, context.tenant_id)['is_guest']:
                from sqlalchemy import select, func
                from .tenancy.database import db_connection, resources
                with db_connection() as connection:
                    count = connection.execute(select(func.count()).select_from(resources).where(
                        resources.c.tenant_id == context.tenant_id, resources.c.kind == 'media')).scalar_one()
                if count >= 8:
                    raise HTTPException(429, '保存到你的门店后，可以继续添加照片和录音')
            result = await process_media(raw, kind, settings, media_store())
            from .resource_access import ensure_live_context
            ensure_live_context()
            return result
    except MediaInputError as exc:
        raise HTTPException(422, str(exc)) from exc
    finally:
        await form.close()


@router.get('/api/media/{identifier}')
async def media_preview(identifier: str):
    record = await asyncio.to_thread(media_store().get, identifier)
    if not record:
        raise HTTPException(404, '这份素材已过期，请重新上传')
    path, mime, _ = record
    return FileResponse(path, media_type=mime, headers={
        'Cache-Control': 'private, no-store', 'X-Content-Type-Options': 'nosniff',
        'Content-Disposition': 'inline; filename="capture' + path.suffix + '"',
        'Content-Security-Policy': "default-src 'none'; sandbox"})
