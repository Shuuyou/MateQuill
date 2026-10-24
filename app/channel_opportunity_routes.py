"""Regional coordination of source-bound store marketing proposals."""
from fastapi import APIRouter
from pydantic import BaseModel, ConfigDict, Field
from . import channel_opportunities as service

router = APIRouter(prefix='/api/channel', tags=['local-marketing-opportunities'])


class PreparationInput(BaseModel):
    model_config = ConfigDict(extra='forbid')
    store_ids: list[str] = Field(default_factory=list, max_length=10)
    refresh: bool = False
    instruction: str = Field(default='', max_length=600)


class CandidateInput(BaseModel):
    model_config = ConfigDict(extra='forbid')
    candidate_id: str = Field(min_length=1, max_length=40)


@router.get('/campaigns/{identifier}/opportunities')
def opportunities(identifier: str):
    return service.campaign_opportunities(identifier)


@router.post('/campaigns/{identifier}/opportunities/prepare')
async def prepare(identifier: str, payload: PreparationInput):
    return await service.prepare_campaign(identifier, **payload.model_dump())


@router.post('/assignments/{identifier}/opportunity/adopt')
async def adopt(identifier: str):
    return await service.adopt_opportunity(identifier)


@router.post('/assignments/{identifier}/opportunity/select')
def select_candidate(identifier: str, payload: CandidateInput):
    return service.select_opportunity_candidate(identifier, payload.candidate_id)


@router.get('/assignments/{identifier}/opportunity')
def assignment_opportunity(identifier: str):
    return service.assignment_opportunity(identifier)
