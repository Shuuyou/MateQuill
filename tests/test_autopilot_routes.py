import asyncio
import os

from sqlalchemy import delete

from app import channel_routes as routes
from app.store_autopilot import opportunity_cache_key
from app.tenancy import save_body
from app.tenancy.database import db_connection, resources
from test_store_autopilot import stores, acting
import pytest

pytestmark = pytest.mark.real_tenancy


def remove_cached_opportunity(owner):
    key = opportunity_cache_key(owner['assignment']['id'], owner['package']['version'])
    with db_connection() as connection:
        connection.execute(delete(resources).where(resources.c.kind == 'store_opportunity',
            resources.c.id == key, resources.c.tenant_id == owner['context'].tenant_id))
    return key


def test_background_research_then_dispatch_without_second_click(stores, monkeypatch):
    owner = stores[0]
    calls = []

    async def scenario():
        gate = asyncio.Event()
        async def planner(identifier, **kwargs):
            calls.append(identifier)
            await gate.wait()
            return owner['prepared']
        monkeypatch.setattr('app.store_opportunity_agent.plan_store_opportunity', planner)
        with acting(owner):
            key = remove_cached_opportunity(owner)
            first = await routes.start_draft(owner['assignment']['id'], routes.PrepareInput())
            second = await routes.start_draft(owner['assignment']['id'], routes.PrepareInput())
            assert first['status'] == second['status'] == 'preparing'
            worker = routes._draft_tasks[key]
            gate.set()
            await worker
            result = routes.draft_state(owner['assignment']['id'])
            assert result['status'] == 'queued'
            assert result['task'] == owner['prepared']['task']
            repeated = await routes.start_draft(owner['assignment']['id'], routes.PrepareInput())
            assert repeated['job_id'] == result['job_id']
            assert len(calls) == 1
    asyncio.run(scenario())


def test_failed_preparation_does_not_loop_or_enqueue(stores, monkeypatch):
    owner = stores[0]
    calls = []
    async def planner(identifier, **kwargs):
        calls.append(identifier)
        return {'status': 'unavailable'}
    monkeypatch.setattr('app.store_opportunity_agent.plan_store_opportunity', planner)

    async def scenario():
        with acting(owner):
            key = remove_cached_opportunity(owner)
            await routes.start_draft(owner['assignment']['id'], routes.PrepareInput())
            await routes._draft_tasks[key]
            assert routes.draft_state(owner['assignment']['id'])['status'] == 'failed'
            for _ in range(3):
                assert (await routes.start_draft(owner['assignment']['id'], routes.PrepareInput()))['status'] == 'failed'
            assert calls == [owner['assignment']['id']]
    asyncio.run(scenario())


def test_restart_during_research_exposes_retry_instead_of_fake_progress(stores):
    owner = stores[0]
    with acting(owner):
        key = remove_cached_opportunity(owner)
        import time
        save_body('store_draft_preparation', key, {'status': 'preparing', 'phase': 'planning',
            'started_at': time.time(), 'process_id': os.getpid() + 1, 'job_id': None},
            store_id=owner['store']['id'])
        assert routes.draft_state(owner['assignment']['id'])['status'] == 'failed'
