"""Small store-facing contract for durable, editable design delivery."""
from __future__ import annotations

import asyncio
import json
from uuid import uuid4
from fastapi import APIRouter, HTTPException
from fastapi.responses import Response
from pydantic import BaseModel, ConfigDict, Field
from . import design_service as service

router = APIRouter(prefix='/api/designs', tags=['store-design'])


class DesignInput(BaseModel):
    model_config = ConfigDict(extra='forbid')
    source_run_id: str = Field(min_length=1, max_length=120)
    platform: str = Field(default='', max_length=40)
    surface: str = Field(default='', max_length=40)
    instruction: str = Field(default='', max_length=2000)
    idempotency_key: str = Field(default_factory=lambda: uuid4().hex, min_length=8, max_length=100)


class RevisionInput(BaseModel):
    model_config = ConfigDict(extra='forbid')
    instruction: str = Field(min_length=1, max_length=2000)
    idempotency_key: str = Field(default_factory=lambda: uuid4().hex, min_length=8, max_length=100)


@router.post('', status_code=202)
def create_design(payload: DesignInput):
    return service.enqueue_design(**payload.model_dump())


@router.get('/{identifier}')
def read_design(identifier: str):
    return service.project_design(service.load_design(identifier))


@router.post('/{identifier}/revise', status_code=202)
def revise_design(identifier: str, payload: RevisionInput):
    parent = service.load_design(identifier, edit=True)
    return service.enqueue_design(parent['source_run_id'], parent_id=identifier,
        platform=parent['platform'], **payload.model_dump())


@router.get('/{identifier}/artboards/{board_id}/preview.svg')
async def preview(identifier: str, board_id: str):
    from .design import render_design_svg
    doc = service.artboard(identifier, board_id)
    svg = await asyncio.to_thread(render_design_svg, doc, lambda aid: doc.assets.get(aid, {}).get('data_url', ''))
    return Response(svg, media_type='image/svg+xml', headers={'Cache-Control': 'private, no-store',
        'X-Content-Type-Options': 'nosniff', 'Content-Security-Policy': "default-src 'none'; img-src data:; style-src 'unsafe-inline'"})


@router.get('/{identifier}/artboards/{board_id}/export')
async def export_artboard(identifier: str, board_id: str, format: str = 'png'):
    if format not in {'png', 'svg', 'json'}:
        raise HTTPException(422, '请选择 PNG、SVG 或设计文件')
    doc = service.artboard(identifier, board_id)
    if format == 'png':
        raw = await service.render_document(doc)
        mime = 'image/png'
    elif format == 'svg':
        from .design import render_design_svg
        raw = (await asyncio.to_thread(render_design_svg, doc,
               lambda aid: doc.assets.get(aid, {}).get('data_url', ''))).encode()
        mime = 'image/svg+xml'
    else:
        raw = json.dumps(doc.model_dump(mode='json'), ensure_ascii=False, indent=2).encode()
        mime = 'application/json'
    service.load_design(identifier)  # Recheck access after potentially slow rendering.
    return Response(raw, media_type=mime, headers={'Cache-Control': 'private, no-store',
        'X-Content-Type-Options': 'nosniff', 'Content-Disposition': f'attachment; filename="matequill-{board_id}.{format}"'})
