"""Reusable creative-planning HTTP boundary; never publishes to social platforms."""
from fastapi import APIRouter, HTTPException
from pydantic import ValidationError

from .models import TaskInput, EvidenceBundle

router = APIRouter(prefix='/api/creative', tags=['creative-connectors'])


@router.get('/connectors')
async def connector_catalog(platform: str | None = None):
    from .creative_connectors import list_connectors
    items = list_connectors()
    if platform is not None:
        items = [item for item in items if item.get('platform') == platform]
    return {'items': items, 'publishes_content': False}


@router.post('/brief')
async def creative_brief(task: TaskInput):
    """Preview strategy without calling a model or accessing private knowledge."""
    from .creative_connectors import get_connector
    try:
        connector = get_connector(task.platform, getattr(task,'creative_connector','auto'))
        brief = connector.build_brief(task, EvidenceBundle(product=task.product), {})
    except (KeyError, ValueError, ValidationError):
        raise HTTPException(422, '创意连接器或风格与所选平台不匹配') from None
    return {'connector': connector.describe(), 'brief': brief,
            'evidence_status': 'not_retrieved', 'model_called': False,
            'note': '这是创意策略预览；产品事实、图片和最终文案需经生成流程取得并核验。'}
