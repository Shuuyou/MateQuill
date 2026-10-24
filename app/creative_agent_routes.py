"""Authenticated research, stateful revision and explicit adoption boundaries."""
import asyncio
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, ConfigDict, Field

router = APIRouter(prefix='/api/agent', tags=['creative-agent'])
_gate = asyncio.Semaphore(2)


def model(*, calls=4, tokens=2400):
    from .config import settings
    from .content_models import build_content_model
    from .llm import ModelCallError
    timeout = 90 if all((settings.content_base_url, settings.content_api_key, settings.content_model)) else 45
    try:
        result = build_content_model(calls=calls, tokens=tokens, timeout=timeout, configuration=settings)
    except ModelCallError:
        raise HTTPException(503, '创作服务连接尚未完整设置，请联系团队管理员') from None
    if not result.enabled:
        raise HTTPException(503, '创作服务尚未连接')
    return result


class RevisionInput(BaseModel):
    model_config = ConfigDict(extra='forbid')
    instruction: str = Field(min_length=1, max_length=2000)
    selection: list[str] | None = Field(default=None, max_length=16)


@router.post('/runs/{run_id}/revise')
async def revise(run_id: str, payload: RevisionInput):
    from .creative_revision import revise_creation
    from .api import run_view
    from .config import settings
    deadline = 210 if all((settings.content_base_url, settings.content_api_key, settings.content_model)) else 135
    async with _gate:
        record = await asyncio.wait_for(revise_creation(run_id, payload.instruction,
            selection=payload.selection, llm=model(calls=3, tokens=4000)), timeout=deadline)
    return run_view(record, record['run_id'])


@router.post('/runs/{run_id}/adopt')
def adopt(run_id: str):
    from .creative_memory import record_creative_memory
    memory = record_creative_memory(run_id, '采用了这份内容；不代表已发表或产生经营效果', event='adopted')
    return {'ok': True, 'memory_id': memory['id']}
