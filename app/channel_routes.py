"""Channel collaboration routes. Identity and scope come from tenant middleware."""
import asyncio
from collections import defaultdict, deque
import os
import time
from uuid import uuid4

from fastapi import APIRouter, HTTPException
from fastapi.responses import Response
from pydantic import BaseModel, ConfigDict, Field
from . import channel_workflows as service

router = APIRouter(prefix='/api/channel', tags=['channel-collaboration'])
_preparations = {}
_prepare_history = defaultdict(deque)
_draft_tasks = {}


class PrepareInput(BaseModel):
    model_config = ConfigDict(extra='forbid')
    refresh: bool = False
    instruction: str = Field(default='', max_length=600)


@router.post('/assignments/{identifier}/prepare')
async def prepare_assignment(identifier: str, payload: PrepareInput):
    """Prepare this store's opportunity autonomously, without a profile form."""
    from .tenancy import require_context, load_body, save_body
    from .resource_access import ensure_live_context
    from .store_opportunity_agent import plan_store_opportunity
    context = require_context()
    _, assignment, package = service._assignment(identifier)
    if context.store_id != assignment['store_id']:
        raise HTTPException(409, '先切换到这家门店，再准备本店内容')
    from .store_autopilot import opportunity_cache_key
    key = opportunity_cache_key(identifier, package['version'])
    try:
        cached = load_body('store_opportunity', key)
    except HTTPException as exc:
        if exc.status_code != 404:
            raise
        cached = None
    if cached and not payload.refresh and time.time() - cached['prepared_at'] < 1800:
        return {**cached['result'], 'cache_hit': True}
    history = _prepare_history[key]
    while history and time.monotonic() - history[0] > 60:
        history.popleft()
    if len(history) >= 6:
        raise HTTPException(429, '正在准备其他角度，稍后再试')
    pending = _preparations.get(key)
    if pending is None:
        history.append(time.monotonic())
        instruction = payload.instruction
        if payload.refresh and cached:
            previous = cached['result'].get('opportunity', {})
            instruction += '\n上次选题：' + str(previous.get('title', ''))[:200] + '。请选择不同的具体切角。'
        pending = asyncio.create_task(plan_store_opportunity(identifier, instruction=instruction))
        _preparations[key] = pending
        pending.add_done_callback(lambda done: _preparations.pop(key, None) if _preparations.get(key) is done else None)
    try:
        result = await asyncio.wait_for(asyncio.shield(pending), timeout=95)
    except asyncio.TimeoutError:
        raise HTTPException(504, '这次选题准备较久，可以稍后重试') from None
    ensure_live_context()
    service._assignment(identifier)  # recheck revoked/expired/versioned campaign
    if result.get('status') != 'ready':
        raise HTTPException(503, '选题服务暂时未完成，请重试')
    save_body('store_opportunity', key, {'assignment_id': identifier,
        'campaign_version': package['version'], 'prepared_at': time.time(), 'result': result},
        store_id=assignment['store_id'], visibility='store')
    return result


def _draft_scope(identifier):
    from .store_autopilot import _scope
    return _scope(identifier)


def _preparation_state(key):
    from .tenancy import load_body
    try:
        return load_body('store_draft_preparation', key)
    except HTTPException as exc:
        if exc.status_code != 404:
            raise
        return None


def _preparation_view(state):
    if state.get('status') == 'preparing' and (state.get('process_id') != os.getpid()
            or time.time() - state['started_at'] > 125):
        return {'status': 'failed', 'phase': 'planning', 'job_id': None,
                'error': {'message': '这次准备已中断，点重试就可以接着准备。'}}
    return {key: state.get(key) for key in ('status', 'phase', 'job_id', 'error') if key in state}


async def _prepare_draft(identifier, key, operation, payload):
    from .tenancy import save_body
    from .resource_access import ensure_live_context
    from .store_autopilot import ensure_store_draft
    try:
        prepared = await prepare_assignment(identifier, payload)
        ensure_live_context()
        _, assignment, _, current_key = _draft_scope(identifier)
        if current_key != key:
            raise HTTPException(409, '活动资料已更新')
        await ensure_store_draft(identifier, prepared, refresh=payload.refresh)
        save_body('store_draft_preparation', key, {'operation_id': operation,
            'status': 'dispatched', 'phase': 'generation', 'started_at': time.time()},
            store_id=assignment['store_id'], visibility='private')
    except asyncio.CancelledError:
        # A restarted process is recognized by process_id on the next read.
        raise
    except Exception as exc:
        try:
            _, assignment, _, current_key = _draft_scope(identifier)
            state = _preparation_state(key)
            if current_key == key and state and state.get('operation_id') == operation:
                message = str(exc.detail) if isinstance(exc, HTTPException) else '这次准备未完成，点重试再试一次。'
                save_body('store_draft_preparation', key, {'operation_id': operation,
                    'status': 'failed', 'phase': 'planning', 'job_id': None,
                    'error': {'message': message}, 'started_at': time.time()},
                    store_id=assignment['store_id'], visibility='private')
        except Exception:
            pass  # revocation must not cause a late write under stale authority


@router.post('/assignments/{identifier}/draft')
async def start_draft(identifier: str, payload: PrepareInput):
    """Start research + real generation once, continuing across page navigation."""
    from .tenancy import save_body
    from .store_autopilot import read_store_draft
    _, assignment, _, key = _draft_scope(identifier)
    state = _preparation_state(key)
    if key in _draft_tasks:
        return {'status': 'preparing', 'phase': 'planning', 'job_id': None}
    existing = read_store_draft(identifier)
    if existing and (not payload.refresh or existing['status'] in ('queued', 'running')):
        return existing
    if state and state.get('status') in ('preparing', 'failed') and not payload.refresh:
        return _preparation_view(state)
    operation = uuid4().hex
    state = {'operation_id': operation, 'status': 'preparing', 'phase': 'planning',
        'job_id': None, 'started_at': time.time(), 'process_id': os.getpid()}
    save_body('store_draft_preparation', key, state, store_id=assignment['store_id'], visibility='private')
    worker = asyncio.create_task(_prepare_draft(identifier, key, operation, payload))
    _draft_tasks[key] = worker
    worker.add_done_callback(lambda done: _draft_tasks.pop(key, None) if _draft_tasks.get(key) is done else None)
    return _preparation_view(state)


@router.get('/assignments/{identifier}/draft')
def draft_state(identifier: str):
    from .store_autopilot import read_store_draft
    _, _, _, key = _draft_scope(identifier)
    state = _preparation_state(key)
    if state and state.get('status') in ('preparing', 'failed'):
        return _preparation_view(state)
    return read_store_draft(identifier) or {'status': 'idle', 'job_id': None}


async def stop_draft_preparations():
    tasks = list(_draft_tasks.values()) + list(_preparations.values())
    for task in tasks:
        task.cancel()
    if tasks:
        await asyncio.gather(*tasks, return_exceptions=True)


@router.get('/campaigns')
def campaigns():
    return {'campaigns': service.list_campaigns(), 'progress': service.campaign_progress()}


@router.post('/campaigns', status_code=201)
def create_campaign(payload: service.CampaignPackageInput):
    return service.create_campaign(payload)


@router.put('/campaigns/{identifier}')
def update_campaign(identifier: str, payload: service.CampaignPackageInput):
    return service.update_campaign(identifier, payload)


@router.post('/campaigns/{identifier}/publish')
def publish_campaign(identifier: str):
    return service.publish_campaign(identifier)


@router.post('/campaigns/{identifier}/withdraw')
def withdraw_campaign(identifier: str):
    return service.withdraw_campaign(identifier)


@router.get('/assignments')
def assignments():
    return {'assignments': service.list_assignments()}


@router.post('/assignments/{identifier}/accept')
def accept_assignment(identifier: str):
    return service.accept_assignment(identifier)


@router.get('/assignments/{identifier}/task')
def task(identifier: str):
    return service.assignment_task(identifier)


@router.get('/submissions')
def submissions():
    return {'submissions': service.list_submissions()}


@router.post('/submissions', status_code=201)
def submit_content(payload: service.SubmissionInput):
    return service.submit_content(payload)


@router.post('/submissions/{identifier}/review')
def review(identifier: str, payload: service.ReviewInput):
    return service.review_submission(identifier, payload)


@router.post('/submissions/{identifier}/publish')
def publication(identifier: str, payload: service.PublicationInput):
    return service.publish_submission(identifier, payload)


@router.get('/grants')
def grants():
    return {'grants': service.list_grants()}


@router.post('/campaigns/{identifier}/grants', status_code=201)
def share(identifier: str, payload: service.GrantInput):
    return service.create_grant(identifier, payload)


@router.post('/grants/accept')
def accept_grant(payload: service.AcceptGrantInput):
    return service.accept_grant(payload)


@router.post('/grants/{identifier}/revoke')
def revoke_grant(identifier: str):
    return service.revoke_grant(identifier)


@router.get('/submissions/{identifier}/assets/{asset_id}')
async def submission_asset(identifier: str, asset_id: str):
    image = service.submission_asset(identifier, asset_id)
    if image is not None:
        return Response(image[0], media_type=image[1], headers={
            'Cache-Control': 'private, no-store', 'X-Content-Type-Options': 'nosniff'})
    from .api import asset
    response = await asset(asset_id)
    response.headers['Cache-Control'] = 'private, no-store'
    return response
