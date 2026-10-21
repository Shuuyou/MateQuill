"""A shared creation grants access only to the exact images embedded in it."""
import hashlib
import re
from fastapi import HTTPException
from fastapi.responses import FileResponse
from sqlalchemy import select
from .tenancy import require_context
from .tenancy.database import db_connection, resources


async def read_parent_asset(meta, snapshot, asset_id):
    if not re.fullmatch(r'[A-Za-z0-9_-]{1,120}', asset_id):
        raise HTTPException(404, '没有找到这张内容配图')
    selected = {image.get('asset_id') for image in snapshot.get('images', []) if isinstance(image, dict)}
    selected.update(aid for card in (snapshot.get('creative_plan') or {}).get('cards', [])
                    for aid in card.get('asset_ids', []))
    declared = next((item for item in snapshot.get('assets', []) if item.get('id') == asset_id), None)
    if asset_id not in selected or not declared:
        raise HTTPException(404, '没有找到这张内容配图')
    if asset_id.startswith('upload-'):
        context = require_context()
        identifier = asset_id.removeprefix('upload-')
        with db_connection() as connection:
            media = connection.execute(select(resources).where(resources.c.kind == 'media',
                resources.c.id == identifier, resources.c.tenant_id == context.tenant_id)).mappings().first()
        if not media or meta['tenant_id'] != context.tenant_id:
            raise HTTPException(404, '没有找到这张内容配图')
        if media['visibility'] != 'tenant' and media['store_id'] != meta['store_id'] and not (
            media['store_id'] is None and media['owner_id'] == meta['owner_id']):
            raise HTTPException(404, '没有找到这张内容配图')
        from .media_routes import media_store
        data = media_store()._read(identifier)
        expected = declared.get('sha256', '')
        if not data or not data[1].startswith('image/') or not re.fullmatch('[0-9a-f]{64}', expected):
            raise HTTPException(404, '这张内容配图已不可用')
        if hashlib.sha256(data[0].read_bytes()).hexdigest() != expected:
            raise HTTPException(404, '这张内容配图已更新')
        return FileResponse(data[0], media_type=data[1], headers={
            'Cache-Control': 'private, no-store', 'X-Content-Type-Options': 'nosniff'})
    from .api import asset
    return await asset(asset_id)
