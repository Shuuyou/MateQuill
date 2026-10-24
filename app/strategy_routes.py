"""Typed strategy management and explicit, scoped user feedback."""
from fastapi import APIRouter, HTTPException

from . import strategy_registry as service
from .tenancy import authorize_resource, list_resources, require_role

router = APIRouter(prefix='/api/strategies', tags=['marketing-strategies'])


@router.get('')
def catalog():
    return {'strategies': service.list_strategy_packs()}


@router.post('', status_code=201)
def create(payload: service.StrategyPack):
    require_role('admin', 'operator')
    return service.save_strategy_pack(payload.model_dump(mode='json'))


@router.post('/evaluate')
def evaluate(payload: service.StrategyPack):
    require_role('admin', 'operator')
    return service.evaluate_strategy_pack(payload.model_dump(mode='json'))


@router.get('/runs/{run_id}')
def run_strategy(run_id: str):
    run = authorize_resource('run', run_id)
    result = (run.get('body') or {}).get('strategy', {})
    return {'run_id': run_id, 'snapshot_id': result.get('snapshot_id'),
            'snapshot': result.get('strategy_snapshot')}


@router.post('/snapshots/{identifier}/feedback', status_code=201)
def feedback(identifier: str, payload: service.StrategyFeedback):
    if payload.source != 'user_report':
        raise HTTPException(422, '此入口记录使用者反馈；平台数据由授权连接器写入')
    return service.record_strategy_feedback(identifier, payload.model_dump(mode='json'))


@router.get('/snapshots/{identifier}/feedback')
def feedback_history(identifier: str):
    authorize_resource('strategy_snapshot', identifier)
    return {'events': [row['body'] for row in list_resources('strategy_feedback')
                       if row['body'].get('snapshot_id') == identifier]}
