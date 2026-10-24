"""Adversarial durable-job tests with real memberships and opaque sessions."""
import asyncio
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from dataclasses import replace
import threading
import time
import os
from uuid import uuid4

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.responses import JSONResponse
from fastapi.testclient import TestClient
from sqlalchemy import select, update, func, delete

from app import generation_jobs as jobs
from app import channel_workflows as channel
from app.models import TaskInput
from app.store_campaigns import StoreInput, StoreRepository
from app.tenancy import set_context, reset_context, save_body, load_body, list_resources, install_tenancy
from app.tenancy.auth import new_session, context_from_session, session_row
from app.tenancy.database import db_connection, memberships, clear_engine_cache, resources
from scripts.tenant_admin import bootstrap

pytestmark = pytest.mark.real_tenancy
PASSWORD = 'Jobs-Test-Only-2026-Strong!'


@pytest.fixture
def identities(tmp_path, monkeypatch):
    if os.getenv('MATEQUILL_JOBS_TEST_BACKEND') == 'postgres':
        target = os.getenv('MATEQUILL_TEST_POSTGRES_URL')
        if not target:
            pytest.skip('Requires independent migrated jobs PostgreSQL test database')
        monkeypatch.setenv('MATEQUILL_DATABASE_URL', target)
    else:
        monkeypatch.setenv('MATEQUILL_DATABASE_URL', 'sqlite:///' + (tmp_path / 'jobs.sqlite3').as_posix())
    clear_engine_cache()
    if os.getenv('MATEQUILL_JOBS_TEST_BACKEND') == 'postgres':
        # This flag is ONLY for an independent disposable test database. The
        # dispatcher deliberately sees control-plane locators across tenants.
        with db_connection() as connection:
            connection.execute(delete(jobs.dispatch))
    result = []
    unique = uuid4().hex
    for suffix in ('a', 'b'):
        account = bootstrap(email=f'jobs-{suffix}-{unique}@example.test', name='测试运营', tenant_name='测试企业'+suffix, password=PASSWORD)
        raw = new_session(account['user_id'], account['tenant_id'])
        context = context_from_session(session_row(raw))
        token = set_context(context)
        try:
            store = StoreRepository().save_store(StoreInput(name='测试门店'+suffix, audience='大学生'))
        finally:
            reset_context(token)
        raw = new_session(account['user_id'], account['tenant_id'], store['id'])
        result.append({'account': account, 'store': store, 'cookie': raw, 'context': context_from_session(session_row(raw))})
    yield result
    clear_engine_cache()


@contextmanager
def acting(identity):
    token = set_context(identity['context'])
    try:
        yield identity['context']
    finally:
        reset_context(token)


def enqueue(identity, *, key=None, task=None):
    with acting(identity):
        return jobs.enqueue(jobs.JobInput(task=task or TaskInput(product='HUAWEI MateBook Pro',
            mode='demo', store_id=identity['store']['id']), idempotency_key=key or uuid4().hex))


def state(identifier):
    with db_connection() as connection:
        return dict(connection.execute(select(jobs.dispatch).where(jobs.dispatch.c.id == identifier)).mappings().one())


def expire(identifier):
    with db_connection() as connection:
        connection.execute(update(jobs.dispatch).where(jobs.dispatch.c.id == identifier).values(lease_until=time.time()-10))


def test_expired_running_lease_can_be_recovered_after_process_restart(identities, monkeypatch):
    owner = identities[0]
    identifier = enqueue(owner)['id']
    original = jobs._claim()
    assert original['id'] == identifier
    expire(identifier)
    recovered = jobs._claim()
    assert recovered and recovered['id'] == identifier
    assert jobs._claim() is None
    calls = []
    async def execute(task, request):
        calls.append(task.store_id)
        return JSONResponse({'run_id': 'recovered-result', 'status': 'ready'})
    monkeypatch.setattr('app.api.run_task', execute)
    asyncio.run(jobs.execute_claimed(recovered))
    assert calls == [owner['store']['id']]
    assert state(identifier)['status'] == 'succeeded'
    with acting(owner):
        assert jobs.job_result(identifier)['result']['run_id'] == 'recovered-result'


def test_persisted_succeeded_body_never_regenerates_after_dispatch_crash(identities, monkeypatch):
    owner = identities[0]
    identifier = enqueue(owner)['id']
    jobs._claim()
    with acting(owner):
        body = load_body('generation_job', identifier)
        body.update(status='succeeded', result={'run_id': 'already-finished', 'status': 'ready'})
        save_body('generation_job', identifier, body)
    expire(identifier)
    row = jobs._claim()
    async def forbidden(*args, **kwargs):
        raise AssertionError('A completed persisted job must not call a provider again')
    monkeypatch.setattr('app.api.run_task', forbidden)
    asyncio.run(jobs.execute_claimed(row))
    assert state(identifier)['status'] == 'succeeded'
    with acting(owner):
        assert jobs.job_result(identifier)['result']['run_id'] == 'already-finished'


def test_concurrent_claims_respect_one_running_job_per_tenant(identities):
    owner, other = identities
    for _ in range(4):
        enqueue(owner)
    with ThreadPoolExecutor(max_workers=6) as pool:
        rows = list(pool.map(lambda _: jobs._claim(), range(6)))
    assert len([row for row in rows if row]) == 1
    other_id = enqueue(other)['id']
    assert jobs._claim()['id'] == other_id


def test_concurrent_budget_reservations_cannot_exceed_tenant_limit(identities, monkeypatch):
    owner = identities[0]
    monkeypatch.setenv('MATEQUILL_DAILY_CREATIONS', '1')
    barrier = threading.Barrier(5)
    def reserve(_):
        with acting(owner):
            barrier.wait(timeout=5)
            try:
                with jobs.generation_budget():
                    return 'completed'
            except HTTPException as exc:
                return exc.status_code
    with ThreadPoolExecutor(max_workers=5) as pool:
        outcomes = list(pool.map(reserve, range(5)))
    assert outcomes.count('completed') == 1
    assert outcomes.count(429) == 4
    with acting(owner):
        summary = jobs.usage_summary()
        assert summary['completed'] == 1 and summary['reserved'] == 0


@pytest.mark.parametrize('result_status', ['blocked', 'error', 'needs_input'])
def test_nonready_persisted_result_releases_success_quota(identities, monkeypatch, result_status):
    owner = identities[0]
    monkeypatch.setenv('MATEQUILL_DAILY_CREATIONS', '1')
    identifier = 'not-ready-' + uuid4().hex
    with acting(owner):
        with jobs.generation_budget():
            assert jobs.usage_summary()['reserved'] == 1
            jobs.persist_generation_result(identifier, {'run_id': identifier, 'status': result_status})
        summary = jobs.usage_summary()
        assert summary['completed'] == 0 and summary['reserved'] == 0 and summary['failed'] == 1
        # Keep the unsuccessful result for recovery/explanation without charging
        # a successful creation. The same one-unit allowance remains available.
        assert load_body('run', identifier)['status'] == result_status
        ready_id = 'next-ready-' + uuid4().hex
        with jobs.generation_budget():
            jobs.persist_generation_result(ready_id, {'run_id': ready_id, 'status': 'ready'})
        assert jobs.usage_summary()['completed'] == 1
        assert jobs.usage_summary()['failed'] == 1
        with pytest.raises(HTTPException) as error:
            with jobs.generation_budget():
                pytest.fail('The one successful creation already consumed the allowance')
        assert error.value.status_code == 429


def test_ready_persisted_result_still_charges_exactly_one_success(identities, monkeypatch):
    owner = identities[0]
    monkeypatch.setenv('MATEQUILL_DAILY_CREATIONS', '1')
    identifier = 'ready-' + uuid4().hex
    with acting(owner):
        with jobs.generation_budget():
            jobs.persist_generation_result(identifier, {'run_id': identifier, 'status': 'ready'})
            assert jobs.usage_summary()['completed'] == 1
        # The budget context finalizer must neither charge twice nor release a
        # successfully persisted run's reservation back to the pool.
        summary = jobs.usage_summary()
        assert summary['completed'] == 1 and summary['reserved'] == 0 and summary['failed'] == 0
        with pytest.raises(HTTPException) as error:
            with jobs.generation_budget():
                pytest.fail('A saved ready result must consume the allowance')
        assert error.value.status_code == 429


def test_revoked_queued_membership_cancels_without_model_use(identities, monkeypatch):
    owner = identities[0]
    identifier = enqueue(owner)['id']
    row = jobs._claim()
    with db_connection() as connection:
        connection.execute(update(memberships).where(memberships.c.user_id == owner['context'].user_id).values(active=False))
    calls = []
    async def forbidden(*args, **kwargs):
        calls.append(True)
        raise AssertionError('Revoked members cannot execute queued generation')
    monkeypatch.setattr('app.api.run_task', forbidden)
    asyncio.run(jobs.execute_claimed(row))
    assert not calls
    assert state(identifier)['status'] == 'cancelled'


def test_withdrawn_campaign_is_rechecked_when_queued_task_executes(identities, monkeypatch):
    owner = identities[0]
    with acting(owner):
        campaign = channel.create_campaign(channel.CampaignPackageInput(title='新品活动', brief='讲生态',
            product='HUAWEI MateBook Pro', store_ids=[owner['store']['id']]))
        channel.publish_campaign(campaign['id'])
        assignment = channel.list_assignments()[0]
        task = TaskInput.model_validate(channel.assignment_task(assignment['id'])['task'])
    identifier = enqueue(owner, task=task)['id']
    with acting(owner):
        channel.withdraw_campaign(campaign['id'])
    calls = []
    async def forbidden(*args, **kwargs):
        calls.append(True)
        raise AssertionError('A withdrawn campaign must stop before provider execution')
    monkeypatch.setattr('app.workflow.AgentWorkflow.run', forbidden)
    asyncio.run(jobs.execute_claimed(jobs._claim()))
    assert not calls
    assert state(identifier)['status'] in {'failed', 'cancelled'}
    with acting(owner):
        assert jobs.job_result(identifier)['status'] in {'failed', 'cancelled'}


def test_polling_uses_object_authorization_not_known_job_id(identities):
    owner, other = identities
    identifier = enqueue(owner)['id']
    application = FastAPI()
    install_tenancy(application)
    application.include_router(jobs.router)
    with TestClient(application) as client:
        client.cookies.set('matequill_session', owner['cookie'])
        response = client.get('/api/jobs/'+identifier)
        assert response.status_code == 200
        assert 'task' not in response.json()
        client.cookies.clear()
        client.cookies.set('matequill_session', other['cookie'])
        assert client.get('/api/jobs/'+identifier).status_code == 404
        client.cookies.clear()
        assert client.get('/api/jobs/'+identifier).status_code == 401


def test_shutdown_cancellation_leaves_work_retryable_after_restart(identities, monkeypatch):
    owner = identities[0]
    identifier = enqueue(owner)['id']
    row = jobs._claim()
    async def run_scenario():
        started = asyncio.Event()
        async def pending(*args, **kwargs):
            started.set()
            await asyncio.Future()
        monkeypatch.setattr('app.api.run_task', pending)
        task = asyncio.create_task(jobs.execute_claimed(row))
        await asyncio.wait_for(started.wait(), timeout=5)
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
    asyncio.run(run_scenario())
    assert state(identifier)['status'] in {'queued', 'running'}
    expire(identifier)
    recovered = jobs._claim()
    assert recovered and recovered['id'] == identifier


def test_poll_does_not_leave_running_body_when_dispatch_is_terminal(identities):
    owner = identities[0]
    identifier = enqueue(owner)['id']
    with acting(owner):
        body = load_body('generation_job', identifier)
        body['status'] = 'running'
        save_body('generation_job', identifier, body)
    with db_connection() as connection:
        connection.execute(update(jobs.dispatch).where(jobs.dispatch.c.id == identifier).values(status='failed'))
    with acting(owner):
        assert jobs.job_result(identifier)['status'] == 'failed'


def test_queue_limit_rejection_does_not_leave_orphan_prompt_resource(identities):
    owner = identities[0]
    for index in range(20):
        enqueue(owner, key=f'queue-limit-{index:03d}')
    with acting(owner):
        before = len(list_resources('generation_job'))
        with pytest.raises(HTTPException) as exc:
            jobs.enqueue(jobs.JobInput(task=TaskInput(product='HUAWEI MateBook Pro', mode='demo',
                store_id=owner['store']['id']), idempotency_key='queue-limit-rejected'))
        assert exc.value.status_code == 429
        assert len(list_resources('generation_job')) == before


def test_concurrent_duplicate_enqueue_owns_one_resource_and_dispatch(identities):
    owner = identities[0]
    barrier = threading.Barrier(4)
    def submit(_):
        barrier.wait(timeout=5)
        return enqueue(owner, key='same-concurrent-request')['id']
    with ThreadPoolExecutor(max_workers=4) as pool:
        identifiers = list(pool.map(submit, range(4)))
    assert len(set(identifiers)) == 1
    with acting(owner):
        assert len(list_resources('generation_job')) == 1


def test_stale_worker_cannot_execute_after_another_worker_reclaims_lease(identities, monkeypatch):
    owner = identities[0]
    identifier = enqueue(owner)['id']
    stale = jobs._claim()
    expire(identifier)
    current = jobs._claim()
    calls = []
    async def provider(*args, **kwargs):
        calls.append(True)
        return JSONResponse({'run_id': 'new-worker-result', 'status': 'ready'})
    monkeypatch.setattr('app.api.run_task', provider)
    asyncio.run(jobs.execute_claimed(stale))
    assert not calls
    assert state(identifier)['status'] == 'running'
    asyncio.run(jobs.execute_claimed(current))
    assert calls == [True]
    assert state(identifier)['status'] == 'succeeded'


def test_stale_recovery_cannot_release_the_new_workers_budget(identities, monkeypatch):
    owner = identities[0]
    identifier = enqueue(owner)['id']
    jobs._claim()
    expire(identifier)
    stale = jobs._claim()  # status=running: this worker is itself a recovery.
    expire(identifier)
    current = jobs._claim()
    reservation = identifier + ':current-worker-reservation'
    with acting(owner):
        with db_connection() as connection:
            connection.execute(jobs.usage.insert().values(id=reservation,
                tenant_id=owner['context'].tenant_id, user_id=owner['context'].user_id,
                store_id=owner['store']['id'], day=jobs._day(), status='reserved', created_at=time.time()))
    calls = []
    async def forbidden(*args, **kwargs):
        calls.append(True)
        return JSONResponse({'status': 'ready'})
    monkeypatch.setattr('app.api.run_task', forbidden)
    asyncio.run(jobs.execute_claimed(stale))
    assert not calls
    assert state(identifier)['status'] == 'running'
    with acting(owner):
        with db_connection() as connection:
            assert connection.execute(select(jobs.usage.c.status).where(jobs.usage.c.id == reservation)).scalar_one() == 'reserved'


def test_reused_idempotency_key_for_different_task_is_conflict(identities):
    owner = identities[0]
    identifier = enqueue(owner, key='one-operation-key')['id']
    with pytest.raises(HTTPException) as exc:
        enqueue(owner, key='one-operation-key', task=TaskInput(product='HUAWEI Mate 80', mode='demo', store_id=owner['store']['id']))
    assert exc.value.status_code == 409
    with acting(owner):
        assert len(list_resources('generation_job')) == 1


def test_postgres_usage_ledger_isolation_and_connection_context_reset(monkeypatch):
    target = os.getenv('MATEQUILL_TEST_POSTGRES_URL')
    if not target:
        pytest.skip('Requires migrated non-owner PostgreSQL test connection')
    monkeypatch.setenv('MATEQUILL_DATABASE_URL', target)
    suffix = uuid4().hex
    first = bootstrap(email='ledger-a-'+suffix+'@example.test', name='甲', tenant_name='用量甲', password=PASSWORD)
    second = bootstrap(email='ledger-b-'+suffix+'@example.test', name='乙', tenant_name='用量乙', password=PASSWORD)
    from app.tenancy import TenantContext
    context_a = TenantContext(first['user_id'], first['tenant_id'], roles=('admin',), all_stores=True, grants=(('admin', None),))
    context_b = TenantContext(second['user_id'], second['tenant_id'], roles=('admin',), all_stores=True, grants=(('admin', None),))
    token = set_context(context_a)
    try:
        with jobs.generation_budget():
            pass
        with db_connection() as connection:
            assert connection.execute(select(func.count()).select_from(jobs.usage).where(jobs.usage.c.tenant_id == first['tenant_id'])).scalar_one() == 1
        set_context(context_b)
        with db_connection() as connection:
            assert connection.execute(select(func.count()).select_from(jobs.usage).where(jobs.usage.c.tenant_id == first['tenant_id'])).scalar_one() == 0
        set_context(None)
        with db_connection() as connection:
            assert connection.execute(select(func.count()).select_from(jobs.usage)).scalar_one() == 0
    finally:
        reset_context(token)


def test_live_membership_revocation_cancels_a_running_provider(identities, monkeypatch):
    owner = identities[0]
    identifier = enqueue(owner)['id']
    row = jobs._claim()
    original_sleep = asyncio.sleep
    async def fast_heartbeat(_):
        await original_sleep(.005)
    monkeypatch.setattr(jobs.asyncio, 'sleep', fast_heartbeat)
    cancelled = []
    async def provider(*args, **kwargs):
        with db_connection() as connection:
            connection.execute(update(memberships).where(memberships.c.user_id == owner['context'].user_id).values(active=False))
        try:
            await asyncio.Future()
        except asyncio.CancelledError:
            cancelled.append(True)
            raise
    monkeypatch.setattr('app.api.run_task', provider)
    async def scenario():
        await asyncio.wait_for(jobs.execute_claimed(row), timeout=5)
    asyncio.run(scenario())
    assert cancelled == [True]
    assert state(identifier)['status'] == 'cancelled'


def test_campaign_withdrawn_during_generation_does_not_persist_a_run(identities, monkeypatch):
    from types import SimpleNamespace
    owner = identities[0]
    with acting(owner):
        campaign = channel.create_campaign(channel.CampaignPackageInput(title='执行中撤回', brief='讲真实任务',
            product='HUAWEI MateBook Pro', store_ids=[owner['store']['id']]))
        channel.publish_campaign(campaign['id'])
        assignment = channel.list_assignments()[0]
        task = TaskInput.model_validate(channel.assignment_task(assignment['id'])['task'])
    identifier = enqueue(owner, task=task)['id']
    async def workflow(_self, _task):
        channel.withdraw_campaign(campaign['id'])
        return SimpleNamespace(model_dump=lambda **kwargs: {'run_id': 'must-not-persist', 'status': 'ready',
            'title': '不再适用的活动', 'blocks': [], 'assets': [], 'sources': []})
    monkeypatch.setattr('app.workflow.AgentWorkflow.run', workflow)
    asyncio.run(jobs.execute_claimed(jobs._claim()))
    assert state(identifier)['status'] in {'failed', 'cancelled'}
    with acting(owner):
        assert list_resources('run') == []
        assert jobs.usage_summary()['reserved'] == 0


def test_crash_after_run_commit_recovers_same_result_without_double_charge(identities, monkeypatch):
    from types import SimpleNamespace
    owner = identities[0]
    monkeypatch.setenv('MATEQUILL_DAILY_CREATIONS', '1')
    identifier = enqueue(owner)['id']
    row = jobs._claim()
    calls = []
    result_id = uuid4().hex
    async def workflow(self, task):
        calls.append(True)
        body = {'run_id': result_id, 'status': 'ready', 'title': '崩溃恢复回归',
                'blocks': [], 'assets': [], 'sources': []}
        return SimpleNamespace(model_dump=lambda **kwargs: body)
    monkeypatch.setattr('app.workflow.AgentWorkflow.run', workflow)
    finish = jobs._finish
    def process_crash(row, body, final):
        if final == 'succeeded':
            raise RuntimeError('Simulated process loss before dispatch completion')
        return finish(row, body, final)
    monkeypatch.setattr(jobs, '_finish', process_crash)
    with pytest.raises(RuntimeError):
        asyncio.run(jobs.execute_claimed(row))
    with acting(owner):
        assert len(list_resources('run')) == 1
        assert jobs.usage_summary()['completed'] == 1
    monkeypatch.setattr(jobs, '_finish', finish)
    expire(identifier)
    asyncio.run(jobs.execute_claimed(jobs._claim()))
    with acting(owner):
        result = jobs.job_result(identifier)
        assert result['status'] == 'succeeded'
        assert result['result']['run_id'] == result_id
        assert len(list_resources('run')) == 1
        assert jobs.usage_summary()['completed'] == 1
    assert calls == [True]
