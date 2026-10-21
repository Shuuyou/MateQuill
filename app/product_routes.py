"""Product onboarding and ecosystem planning routes."""
import asyncio
from fastapi import APIRouter, HTTPException, Request
import httpx
from pydantic import BaseModel, Field

from .product_knowledge import (ProductPreviewInput, ProductConfirmInput, ProductRepository,
                                load_products, summary, source_preview, resolve_ecosystem)

router = APIRouter(prefix='/api', tags=['product-knowledge'])
_ingestion_slots = asyncio.Semaphore(2)


@router.get('/knowledge/products')
def products():
    return {'products': [summary(row) for row in load_products()]}


def require_editor(request):
    from .tenancy import get_context, require_role
    context = get_context()
    if context and not context.is_legacy:
        require_role('admin', 'operator')
        return
    from .api import local_settings_allowed
    if not local_settings_allowed(request):
        raise HTTPException(403, '请在门店管理端确认产品资料')


@router.post('/knowledge/preview')
async def preview(payload: ProductPreviewInput, request: Request):
    require_editor(request)
    try:
        async with _ingestion_slots:
            return await source_preview(payload)
    except (ValueError, httpx.HTTPError) as exc:
        raise HTTPException(422, str(exc) if isinstance(exc, ValueError) else '暂时没有读到资料，请粘贴产品原文继续') from None


@router.post('/knowledge/confirm')
def confirm(payload: ProductConfirmInput, request: Request):
    require_editor(request)
    if not payload.confirmed:
        raise HTTPException(422, '确认事实与来源后再加入产品库')
    try:
        return summary(ProductRepository().confirm(payload.draft_id))
    except KeyError:
        raise HTTPException(404, '这份待确认资料已更新，请重新导入') from None


class EcosystemInput(BaseModel):
    products: list[str] = Field(min_length=2, max_length=4)
    scene: str = Field(default='', max_length=1500)
    audience: str = Field(default='', max_length=200)
    platform: str = Field(default='xiaohongshu', pattern='^(xiaohongshu|wechat|moments|poster)$')


@router.post('/ecosystem/plan')
def ecosystem_plan(payload: EcosystemInput):
    try:
        return resolve_ecosystem(**payload.model_dump())
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from None
