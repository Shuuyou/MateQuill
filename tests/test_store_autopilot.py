"""Real tenant boundaries and concurrent durable store draft dispatch."""
import asyncio
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from copy import deepcopy
import time
from uuid import uuid4

import pytest
from fastapi import HTTPException
from sqlalchemy import update

from app import channel_workflows as channel, generation_jobs as jobs
from app.store_autopilot import ensure_store_draft, read_store_draft, opportunity_cache_key
from app.store_campaigns import StoreInput, StoreRepository
from app.tenancy import set_context, reset_context, save_body, load_body
from app.tenancy.auth import new_session, context_from_session, session_row
from app.tenancy.database import clear_engine_cache, db_connection
from scripts.tenant_admin import bootstrap

pytestmark = pytest.mark.real_tenancy


@contextmanager
def acting(identity):
    token = set_context(identity['context'])
    try:
        yield
    finally:
        reset_context(token)


@pytest.fixture
def stores(tmp_path, monkeypatch):
    monkeypatch.setenv('MATEQUILL_DATABASE_URL', 'sqlite:///' + (tmp_path / 'autopilot.sqlite3').as_posix())
    clear_engine_cache()
    result = []
    for number in range(2):
        account = bootstrap(email=f'autopilot-{number}-{uuid4().hex}@example.test', name='测试运营',
            tenant_name='测试企业', password='Autopilot-Test-Only-Strong-2026!')
        raw = new_session(account['user_id'], account['tenant_id'])
        identity = {'context': context_from_session(session_row(raw))}
        with acting(identity):
            store = StoreRepository().save_store(StoreInput(name='门店', audience='大学生'))
        raw = new_session(account['user_id'], account['tenant_id'], store['id'])
        identity.update(context=context_from_session(session_row(raw)), store=store)
        with acting(identity):
            package = channel.create_campaign(channel.CampaignPackageInput(title='新品体验',
                brief='体验真实产品', product='HUAWEI MateBook Pro', store_ids=[store['id']]))
            channel.publish_campaign(package['id'])
            assignment = channel.list_assignments()[0]
            prepared = {'status': 'ready', 'task': assignment['task'], 'opportunity': {'title': '实际操作'}}
            key = opportunity_cache_key(assignment['id'], package['version'])
            save_body('store_opportunity', key, {'assignment_id': assignment['id'],
                'campaign_version': package['version'], 'prepared_at': time.time(), 'result': prepared},
                store_id=store['id'], visibility='store')
        identity.update(package=package, assignment=assignment, prepared=prepared)
        result.append(identity)
    yield result
    clear_engine_cache()


def ensure(identity, **kwargs):
    with acting(identity):
        return asyncio.run(ensure_store_draft(identity['assignment']['id'], identity['prepared'], **kwargs))


def test_refresh_and_concurrent_calls_queue_one_job(stores):
    owner = stores[0]
    with ThreadPoolExecutor(max_workers=5) as pool:
        rows = list(pool.map(lambda _: ensure(owner), range(5)))
    assert len({row['job_id'] for row in rows}) == 1
    assert all(row['status'] == 'queued' for row in rows)
    assert ensure(owner, refresh=True)['job_id'] == rows[0]['job_id']
    with acting(owner):
        assert read_store_draft(owner['assignment']['id'])['job_id'] == rows[0]['job_id']
        assert channel.list_assignments()[0]['claimed_by'] == owner['context'].user_id


def test_rejects_client_modified_task_and_cross_tenant(stores):
    owner, other = stores
    forged = deepcopy(owner['prepared'])
    forged['task']['audience'] = '伪造的Agent任务'
    with acting(owner), pytest.raises(HTTPException) as exc:
        asyncio.run(ensure_store_draft(owner['assignment']['id'], forged))
    assert exc.value.status_code == 409
    with acting(other), pytest.raises(HTTPException) as exc:
        asyncio.run(ensure_store_draft(owner['assignment']['id'], owner['prepared']))
    assert exc.value.status_code == 404


def test_failure_needs_explicit_retry_and_parallel_retry_is_once(stores):
    owner = stores[0]
    first = ensure(owner)
    with acting(owner), db_connection() as connection:
        connection.execute(update(jobs.dispatch).where(jobs.dispatch.c.id == first['job_id']).values(status='failed'))
    assert ensure(owner)['status'] == 'failed'
    with ThreadPoolExecutor(max_workers=4) as pool:
        rows = list(pool.map(lambda _: ensure(owner, refresh=True), range(4)))
    assert len({row['job_id'] for row in rows}) == 1
    assert rows[0]['job_id'] != first['job_id']
    assert rows[0]['attempt'] == 2


def test_completed_job_recovers_result_without_new_generation(stores):
    owner = stores[0]
    first = ensure(owner)
    with acting(owner):
        body = load_body('generation_job', first['job_id'])
        body.update(status='succeeded', result={'status': 'ready', 'run_id': 'real-run-test'})
        save_body('generation_job', first['job_id'], body)
        with db_connection() as connection:
            connection.execute(update(jobs.dispatch).where(jobs.dispatch.c.id == first['job_id']).values(status='succeeded'))
    clear_engine_cache()  # Process-local state is not used for idempotency.
    result = ensure(owner, refresh=True)
    assert result['status'] == 'ready'
    assert result['run_id'] == 'real-run-test'
    assert result['job_id'] == first['job_id']


def test_withdrawn_campaign_cannot_return_or_dispatch_draft(stores):
    owner = stores[0]
    ensure(owner)
    with acting(owner):
        channel.withdraw_campaign(owner['package']['id'])
        with pytest.raises(HTTPException):
            read_store_draft(owner['assignment']['id'])
    with pytest.raises(HTTPException):
        ensure(owner)


def test_rejects_old_preparation_after_campaign_version_update(stores):
    owner = stores[0]
    with acting(owner):
        channel.update_campaign(owner['package']['id'], channel.CampaignPackageInput(title='新版活动',
            brief='新版资料', product='HUAWEI MateBook Pro', store_ids=[owner['store']['id']]))
        channel.publish_campaign(owner['package']['id'])
    with pytest.raises(HTTPException) as exc:
        ensure(owner)
    assert exc.value.status_code == 409


def test_worker_completed_without_ready_content_is_not_ready(stores):
    owner = stores[0]
    first = ensure(owner)
    with acting(owner):
        body = load_body('generation_job', first['job_id'])
        body.update(status='succeeded', result={'status': 'needs_review', 'run_id': 'incomplete-test'})
        save_body('generation_job', first['job_id'], body)
        with db_connection() as connection:
            connection.execute(update(jobs.dispatch).where(jobs.dispatch.c.id == first['job_id']).values(status='succeeded'))
        assert read_store_draft(owner['assignment']['id'])['status'] == 'needs_revision'
    assert ensure(owner)['job_id'] == first['job_id']
    assert ensure(owner, refresh=True)['job_id'] != first['job_id']


def test_queue_failure_rolls_back_assignment_acceptance(stores, monkeypatch):
    owner = stores[0]
    with acting(owner):
        original = channel.list_assignments()[0]
    def unavailable(*args, **kwargs):
        raise HTTPException(429, '本企业正在处理的创作较多，请稍后再试')
    monkeypatch.setattr(jobs, 'enqueue', unavailable)
    with pytest.raises(HTTPException) as exc:
        ensure(owner)
    assert exc.value.status_code == 429
    with acting(owner):
        assert read_store_draft(owner['assignment']['id']) is None
        assert channel.list_assignments()[0] == original


def test_changed_angle_waits_for_inflight_then_dispatches_new_task(stores):
    owner = stores[0]
    original_task = deepcopy(owner['prepared']['task'])
    first = ensure(owner)
    owner['prepared'] = deepcopy(owner['prepared'])
    owner['prepared']['task']['audience'] = '办公用户'
    owner['prepared']['opportunity']['title'] = '新的真实切角'
    with acting(owner):
        key = opportunity_cache_key(owner['assignment']['id'], owner['package']['version'])
        save_body('store_opportunity', key, {'assignment_id': owner['assignment']['id'],
            'campaign_version': owner['package']['version'], 'result': owner['prepared']},
            store_id=owner['store']['id'], visibility='store')
    pending = ensure(owner, refresh=True)
    assert pending['job_id'] == first['job_id']
    assert pending['task'] == original_task
    with acting(owner):
        body = load_body('generation_job', first['job_id'])
        body.update(status='succeeded', result={'status': 'ready', 'run_id': 'first-angle'})
        save_body('generation_job', first['job_id'], body)
        with db_connection() as connection:
            connection.execute(update(jobs.dispatch).where(jobs.dispatch.c.id == first['job_id']).values(status='succeeded'))
    changed = ensure(owner, refresh=True)
    assert changed['job_id'] != first['job_id']
    assert changed['task']['audience'] == '办公用户'
    assert changed['opportunity']['title'] == '新的真实切角'
