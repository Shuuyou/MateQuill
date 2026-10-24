"""HTTP recovery of saved designs uses the original fenced queue and live grants."""
import asyncio
import base64
import hashlib
import io
from concurrent.futures import ThreadPoolExecutor
from uuid import uuid4

import pytest
from fastapi import FastAPI
from fastapi.responses import JSONResponse
from fastapi.testclient import TestClient
from PIL import Image
from sqlalchemy import delete, update

from app import generation_jobs as jobs
from app.models import TaskInput
from app.store_campaigns import StoreInput, StoreRepository
from app.tenancy import install_tenancy, load_body, save_body
from app.tenancy.auth import new_session, context_from_session, session_row
from app.tenancy.database import db_connection, resources
from test_generation_jobs_durability import identities, acting, state

pytestmark = pytest.mark.real_tenancy


@pytest.fixture
def saved_design(identities):
    owner = identities[0]
    stream = io.BytesIO()
    Image.new('RGB', (16, 16), 'green').save(stream, 'PNG')
    raw = stream.getvalue()
    task = TaskInput(product='HUAWEI Mate 90', mode='demo', store_id=owner['store']['id'])
    source_id, design_id = uuid4().hex, uuid4().hex
    work = {'id': design_id, 'source_run_id': source_id, 'commercial_visuals': {
        'cover': {'status': 'completed', 'asset': {'data_url': 'data:image/png;base64,' + base64.b64encode(raw).decode(),
            'sha256': hashlib.sha256(raw).hexdigest()},
            'reference_review': {'review_completed': False}}}}
    with acting(owner):
        save_body('run', source_id, {'run_id': source_id, 'status': 'ready', '_poster_task': task.model_dump()})
        identifier = jobs.enqueue_operation(jobs.JobInput(task=task), operation='design',
            parameters={'design_id': design_id, 'source_run_id': source_id})['id']
        body = load_body('generation_job', identifier)
        body.update(status='failed', error={'message': '原图审阅未完成'}, finished_at=10)
        save_body('generation_job', identifier, body)
        save_body('design_work', design_id, work)
    with db_connection() as connection:
        connection.execute(update(jobs.dispatch).where(jobs.dispatch.c.id == identifier).values(status='failed'))
    app = FastAPI()
    install_tenancy(app)
    app.include_router(jobs.router)
    return {'owner': owner, 'other': identities[1], 'app': app, 'id': identifier,
            'design_id': design_id, 'source_id': source_id, 'work': work}


def post_retry(case, cookie=None):
    with TestClient(case['app']) as client:
        client.cookies.set('matequill_session', cookie or case['owner']['cookie'])
        return client.post('/api/jobs/' + case['id'] + '/retry', headers={'Origin': 'http://testserver'})


def test_http_retry_preserves_original_job_png_and_queue_lease_with_new_session(saved_design, monkeypatch):
    case = saved_design
    owner = case['owner']
    cookie = new_session(owner['account']['user_id'], owner['account']['tenant_id'], owner['store']['id'])
    response = post_retry(case, cookie)
    assert response.status_code == 202
    assert response.json() == {'id': case['id'], 'status': 'queued'}
    assert state(case['id'])['session_hash'] == context_from_session(session_row(cookie)).session_hash
    with acting(owner):
        body = load_body('generation_job', case['id'])
        assert body['retry_count'] == 1 and 'error' not in body
        assert body['error_history'][0]['error']['message'] == '原图审阅未完成'
        assert load_body('design_work', case['design_id']) == case['work']
    assert post_retry(case, cookie).status_code == 409
    calls = []
    async def resume(parameters):
        calls.append(parameters)
        assert load_body('design_work', parameters['design_id']) == case['work']
        return JSONResponse({'id': parameters['design_id'], 'status': 'ready'})
    async def forbidden_image(*args, **kwargs):
        pytest.fail('Recovery must not invoke the image provider')
    monkeypatch.setattr('app.design_service.execute_design', resume)
    monkeypatch.setattr('app.image_service.ImageService.edit', forbidden_image)
    claimed = jobs._claim()
    assert claimed['id'] == case['id']
    asyncio.run(jobs.execute_claimed(claimed))
    assert calls == [{'design_id': case['design_id'], 'source_run_id': case['source_id']}]
    assert state(case['id'])['status'] == 'succeeded'


@pytest.mark.parametrize('stage_status', ['started', 'unavailable', 'repair_pending'])
def test_unknown_or_unsaved_image_request_cannot_be_requeued(saved_design, stage_status):
    case = saved_design
    with acting(case['owner']):
        work = load_body('design_work', case['design_id'])
        work['commercial_visuals']['cover']['status'] = stage_status
        save_body('design_work', case['design_id'], work)
    assert post_retry(case).status_code == 409
    assert state(case['id'])['status'] == 'failed'


def test_saved_first_page_does_not_allow_repurchase_of_second_unknown_request(saved_design):
    case = saved_design
    with acting(case['owner']):
        work = load_body('design_work', case['design_id'])
        work['commercial_visuals']['second'] = {'status': 'started'}
        save_body('design_work', case['design_id'], work)
    assert post_retry(case).status_code == 409
    assert state(case['id'])['status'] == 'failed'


def test_saved_png_digest_mismatch_is_not_a_recoverable_checkpoint(saved_design):
    case = saved_design
    with acting(case['owner']):
        work = load_body('design_work', case['design_id'])
        work['commercial_visuals']['cover']['asset']['sha256'] = '0' * 64
        save_body('design_work', case['design_id'], work)
    assert post_retry(case).status_code == 409
    assert state(case['id'])['status'] == 'failed'


def test_concurrent_retries_only_queue_one_original_job(saved_design):
    case = saved_design
    with ThreadPoolExecutor(max_workers=2) as pool:
        responses = list(pool.map(lambda _: post_retry(case), range(2)))
    assert sorted(response.status_code for response in responses) == [202, 409]
    with acting(case['owner']):
        assert load_body('generation_job', case['id'])['retry_count'] == 1
    assert state(case['id'])['status'] == 'queued'


def test_cross_tenant_and_other_selected_store_cannot_retry_original_job(saved_design):
    case = saved_design
    assert post_retry(case, case['other']['cookie']).status_code == 403
    owner = case['owner']
    with acting(owner):
        second = StoreRepository().save_store(StoreInput(name='同企业另一门店'))
    cookie = new_session(owner['account']['user_id'], owner['account']['tenant_id'], second['id'])
    assert post_retry(case, cookie).status_code == 403
    assert state(case['id'])['status'] == 'failed'


def test_revoked_source_blocks_retry_and_preserves_failure(saved_design):
    case = saved_design
    with db_connection() as connection:
        connection.execute(delete(resources).where(resources.c.kind == 'run', resources.c.id == case['source_id']))
    assert post_retry(case).status_code == 404
    assert state(case['id'])['status'] == 'failed'
    with acting(case['owner']):
        assert load_body('generation_job', case['id'])['error']['message'] == '原图审阅未完成'


@pytest.mark.parametrize('dispatch_status,operation', [('succeeded', 'design'), ('queued', 'design'), ('running', 'design'), ('failed', 'content')])
def test_only_failed_design_is_retryable(saved_design, dispatch_status, operation):
    case = saved_design
    with acting(case['owner']):
        body = load_body('generation_job', case['id'])
        body['operation'] = operation
        save_body('generation_job', case['id'], body)
    with db_connection() as connection:
        connection.execute(update(jobs.dispatch).where(jobs.dispatch.c.id == case['id']).values(status=dispatch_status))
    assert post_retry(case).status_code == 409
    assert state(case['id'])['status'] == dispatch_status
