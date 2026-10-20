"""Store/campaign HTTP contracts; generation reuses /api/run unchanged."""
from fastapi import APIRouter, HTTPException

from .store_campaigns import CampaignInput, StoreInput, StoreRepository, plan_campaign

router = APIRouter(prefix='/api', tags=['stores-and-campaigns'])


def get_repository():
    return StoreRepository()


@router.get('/stores')
def list_stores():
    return {'stores': get_repository().list_stores()}


@router.post('/stores', status_code=201)
def create_store(payload: StoreInput):
    return get_repository().save_store(payload)


@router.put('/stores/{store_id}')
def update_store(store_id: str, payload: StoreInput):
    try:
        return get_repository().save_store(payload, store_id)
    except KeyError:
        raise HTTPException(status_code=404, detail='未找到这家门店')


@router.post('/campaigns/plan')
def create_plan(payload: CampaignInput):
    repository = get_repository()
    try:
        plan = plan_campaign(payload, repository.list_stores())
        if plan['status'] == 'ready':
            repository.save_campaign(plan)
        return plan
    except KeyError:
        raise HTTPException(status_code=404, detail='所选门店不存在，请重新选择')


@router.get('/campaigns/{campaign_id}')
def read_plan(campaign_id: str):
    try:
        return get_repository().get_campaign(campaign_id)
    except KeyError:
        raise HTTPException(status_code=404, detail='未找到这次推广计划')
