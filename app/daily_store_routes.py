"""Small authenticated API for durable, store-scoped daily agent preparation."""
from fastapi import APIRouter
from pydantic import BaseModel, ConfigDict
from .daily_store_agent import read_daily_proposal, ensure_daily_proposal, daily_action

router = APIRouter(prefix='/api/store/daily', tags=['daily-store-agent'])

class PrepareInput(BaseModel):
    model_config = ConfigDict(extra='forbid')
    refresh: bool = False

@router.get('')
def current():
    return read_daily_proposal()

@router.post('/prepare', status_code=202)
def prepare(payload: PrepareInput):
    return ensure_daily_proposal(refresh=payload.refresh)

@router.post('/{identifier}/adopt')
def adopt(identifier: str):
    return daily_action(identifier, action='adopt')

@router.post('/{identifier}/dismiss')
def dismiss(identifier: str):
    return daily_action(identifier, action='dismiss')
