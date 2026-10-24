"""Live dispatch fencing while a model/design operation awaits remote work."""
import asyncio
from contextlib import contextmanager
import time
from uuid import uuid4

import pytest
from sqlalchemy import select, update

from app.gtm_agent import jobs, runtime
from app.gtm_agent.models import TurnInput
from app.gtm_agent.repository import create_conversation, get_conversation
from app.store_campaigns import StoreInput, StoreRepository
from app.tenancy import set_context, reset_context
from app.tenancy.auth import new_session, context_from_session, session_row
from app.tenancy.database import clear_engine_cache, db_connection, memberships
from scripts.tenant_admin import bootstrap

pytestmark = pytest.mark.real_tenancy


@pytest.fixture
def claimed(tmp_path, monkeypatch):
    monkeypatch.setenv('MATEQUILL_DATABASE_URL', 'sqlite:///' + (tmp_path / 'heartbeat.sqlite').as_posix())
    clear_engine_cache()
    account = bootstrap(email=f'heartbeat-{uuid4().hex}@example.test', name='门店运营',
                        tenant_name='Heartbeat test', password='Heartbeat-Tests-Only-2026-Strong!')
    token = set_context(context_from_session(session_row(new_session(account['user_id'], account['tenant_id']))))
    try:
        store = StoreRepository().save_store(StoreInput(name='租约测试门店'))
    finally:
        reset_context(token)
    raw = new_session(account['user_id'], account['tenant_id'], store['id'])
    context = context_from_session(session_row(raw))
    token = set_context(context)
    try:
        conversation = create_conversation()
        jobs.enqueue_turn(conversation['id'], TurnInput(text='完成门店创作', client_turn_id=uuid4().hex))
    finally:
        reset_context(token)
    yield {'row': jobs.claim_next(), 'context': context, 'conversation': conversation}
    clear_engine_cache()


def dispatch_row(identifier):
    with db_connection() as connection:
        return dict(connection.execute(select(jobs.dispatch).where(jobs.dispatch.c.id == identifier)).mappings().one())


def change_claim(identifier, **values):
    with db_connection() as connection:
        connection.execute(update(jobs.dispatch).where(jobs.dispatch.c.id == identifier).values(**values))


def test_atomic_renewal_rejects_expired_replaced_terminal_and_wrong_tenant_claims(claimed):
    row = claimed['row']
    change_claim(row['id'], lease_until=time.time() + 5)
    assert jobs._renew(row)
    assert dispatch_row(row['id'])['lease_until'] > time.time() + 200
    assert not jobs._renew({**row, 'tenant_id': 'different-tenant'})
    change_claim(row['id'], lease_until=time.time() - 1)
    assert not jobs._renew(row)
    assert dispatch_row(row['id'])['lease_until'] < time.time()
    change_claim(row['id'], lease_until=time.time() + 50, claim_token='replacement')
    assert not jobs._renew(row)
    change_claim(row['id'], claim_token=row['claim_token'], status='completed')
    assert not jobs._renew(row)


def test_waiting_operation_renews_and_cannot_be_claimed_twice(claimed, monkeypatch):
    monkeypatch.setattr(jobs, 'HEARTBEAT_INTERVAL', .01)
    calls = []
    original = jobs._renew

    def renewed(row):
        calls.append(time.time())
        return original(row)

    monkeypatch.setattr(jobs, '_renew', renewed)

    async def scenario():
        started, release = asyncio.Event(), asyncio.Event()

        async def operation(*args, **kwargs):
            started.set()
            await release.wait()
            return {'status': 'completed', 'message': '已完成', 'artifact': None, 'questions': []}

        monkeypatch.setattr(runtime, 'run_turn', operation)
        execution = asyncio.create_task(jobs.execute_claimed(claimed['row']))
        await asyncio.wait_for(started.wait(), 2)
        for _ in range(100):
            if len(calls) >= 3:
                break
            await asyncio.sleep(.01)
        assert len(calls) >= 3
        assert jobs.claim_next() is None
        assert dispatch_row(claimed['row']['id'])['lease_until'] > time.time() + 200
        release.set()
        await asyncio.wait_for(execution, 2)

    asyncio.run(scenario())
    assert dispatch_row(claimed['row']['id'])['status'] == 'completed'


def test_replaced_lease_cancels_operation_without_requeueing_or_finishing_new_owner(claimed, monkeypatch):
    monkeypatch.setattr(jobs, 'HEARTBEAT_INTERVAL', .01)

    async def scenario():
        started, cancelled = asyncio.Event(), asyncio.Event()

        async def operation(*args, **kwargs):
            started.set()
            try:
                await asyncio.Event().wait()
            finally:
                cancelled.set()

        monkeypatch.setattr(runtime, 'run_turn', operation)
        execution = asyncio.create_task(jobs.execute_claimed(claimed['row']))
        await asyncio.wait_for(started.wait(), 2)
        change_claim(claimed['row']['id'], claim_token='replacement-worker', lease_until=time.time() + 300)
        await asyncio.wait_for(execution, 2)
        assert cancelled.is_set()

    asyncio.run(scenario())
    current = dispatch_row(claimed['row']['id'])
    assert current['claim_token'] == 'replacement-worker'
    assert current['status'] == 'running'
    assert current['lease_until'] > time.time() + 250


def test_membership_revocation_cancels_work_without_delivering_assistant_result(claimed, monkeypatch):
    monkeypatch.setattr(jobs, 'HEARTBEAT_INTERVAL', .01)

    async def scenario():
        started, cancelled = asyncio.Event(), asyncio.Event()

        async def operation(*args, **kwargs):
            started.set()
            try:
                await asyncio.Event().wait()
            finally:
                cancelled.set()

        monkeypatch.setattr(runtime, 'run_turn', operation)
        execution = asyncio.create_task(jobs.execute_claimed(claimed['row']))
        await asyncio.wait_for(started.wait(), 2)
        with db_connection() as connection:
            connection.execute(update(memberships).where(memberships.c.user_id == claimed['context'].user_id).values(active=False))
        await asyncio.wait_for(execution, 2)
        assert cancelled.is_set()

    asyncio.run(scenario())
    assert dispatch_row(claimed['row']['id'])['status'] == 'cancelled'
    # Restore membership solely to inspect the unchanged persisted transcript.
    with db_connection() as connection:
        connection.execute(update(memberships).where(memberships.c.user_id == claimed['context'].user_id).values(active=True))
    token = set_context(claimed['context'])
    try:
        conversation = get_conversation(claimed['conversation']['id'])
        assert [message['role'] for message in conversation['messages']] == ['user']
        assert conversation.get('current_artifact') is None
    finally:
        reset_context(token)


def test_external_cancellation_requeues_only_current_live_owner(claimed, monkeypatch):
    async def scenario():
        started = asyncio.Event()

        async def operation(*args, **kwargs):
            started.set()
            await asyncio.Event().wait()

        monkeypatch.setattr(runtime, 'run_turn', operation)
        execution = asyncio.create_task(jobs.execute_claimed(claimed['row']))
        await asyncio.wait_for(started.wait(), 2)
        execution.cancel()
        with pytest.raises(asyncio.CancelledError):
            await execution

    asyncio.run(scenario())
    assert dispatch_row(claimed['row']['id'])['status'] == 'queued'
    replacement = jobs.claim_next()
    assert replacement and replacement['claim_token'] != claimed['row']['claim_token']
