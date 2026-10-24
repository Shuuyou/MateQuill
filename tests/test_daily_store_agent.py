"""Real scoped resources and dispatch, without provider calls or paid images."""
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from datetime import datetime, timezone
from uuid import uuid4
import asyncio

from fastapi import HTTPException, FastAPI
from fastapi.testclient import TestClient
import pytest
from sqlalchemy import select, update

from app import daily_store_agent as daily
from app.gtm_agent import jobs
from app.gtm_agent.repository import TURN
from app.gtm_agent.tools import AgentTools
from app.gtm_agent.models import TurnInput
from app.gtm_agent.repository import get_conversation
from app.store_campaigns import StoreInput, StoreRepository
from app.tenancy import set_context, reset_context, save_body, load_body, register_resource, install_tenancy
from app.tenancy import list_resources
from app.tenancy.auth import new_session, context_from_session, session_row, COOKIE_NAME
from app.tenancy.database import db_connection, clear_engine_cache
from app.daily_store_routes import router
from scripts.tenant_admin import bootstrap

pytestmark = pytest.mark.real_tenancy
NOW = datetime(2026, 10, 24, 2, 0, tzinfo=timezone.utc).timestamp()

@contextmanager
def acting(identity):
    token = set_context(identity['context'])
    try:
        yield
    finally:
        reset_context(token)

@pytest.fixture
def stores(tmp_path, monkeypatch):
    monkeypatch.setenv('MATEQUILL_DATABASE_URL', 'sqlite:///' + (tmp_path/'daily.sqlite').as_posix())
    clear_engine_cache()
    monkeypatch.setattr(daily, '_catalog_revision', lambda: 'verified-official-revision-one')
    identities = []
    for tenant in range(2):
        account = bootstrap(email=f'daily-{uuid4().hex}@example.test', name='运营', tenant_name='测试企业', password='Daily-test-password-2026!')
        raw = new_session(account['user_id'], account['tenant_id'])
        parent = {'context': context_from_session(session_row(raw))}
        with acting(parent):
            shop = StoreRepository().save_store(StoreInput(name='福雷德广场店', location='杭州市钱塘区福雷德广场'))
            other = StoreRepository().save_store(StoreInput(name='来福士店', location='杭州来福士中心'))
        raw = new_session(account['user_id'], account['tenant_id'], shop['id'])
        identities.append({'context': context_from_session(session_row(raw)), 'store': shop, 'other_store': other, 'raw': raw})
    yield identities
    clear_engine_cache()

def ensure(owner, **kwargs):
    with acting(owner):
        return daily.ensure_daily_proposal(now=NOW, **kwargs)

def complete(owner, view, *, status='completed'):
    with acting(owner):
        artifact = None
        if status == 'completed':
            rid = 'daily-run-' + uuid4().hex
            register_resource('run', rid, body={'status': 'ready', 'title': '自习散场，留住这一束灯'})
            artifact = {'kind': 'run', 'id': rid, 'title': '自习散场，留住这一束灯', 'version': 1}
        turn = load_body(TURN, view['turn_id'])
        save_body(TURN, view['turn_id'], {**turn, 'status': status, 'artifact': artifact})
        with db_connection() as connection:
            connection.execute(update(jobs.dispatch).where(jobs.dispatch.c.id == view['turn_id']).values(status=status))

def test_arrival_without_campaign_queues_real_agent_once(stores):
    owner = stores[0]
    counts = []
    def counted(cid, payload, **kwargs):
        counts.append(payload.text)
        return jobs.enqueue_turn(cid, payload, **kwargs)
    first = ensure(owner, enqueue=counted)
    second = ensure(owner, enqueue=counted)
    assert first['turn_id'] == second['turn_id']
    assert len(counts) == 1
    assert first['status'] == 'preparing'
    assert '福雷德广场' in counts[0] and '杭州市钱塘区' in counts[0]
    with acting(owner):
        turn = load_body(TURN, first['turn_id'])
        assert turn['execution_policy']['allow_new_images'] is False
        assert turn['execution_policy']['allow_memory_writes'] is False
        assert turn['input_origin'] == 'daily_store_agent'
        assert '福雷德广场' in turn['input']['text']
        conversation = load_body('agent_conversation', first['conversation_id'])
        assert conversation['messages'][0]['role'] == 'assistant'
        assert conversation['messages'][0]['origin'] == 'daily_store_agent'
        assert '授权门店资料' not in conversation['messages'][0]['text']
        with db_connection() as connection:
            assert connection.execute(select(jobs.dispatch.c.id).where(jobs.dispatch.c.id == first['turn_id'])).scalar_one()
        for _ in range(3):
            assert daily.read_daily_proposal(now=NOW)['turn_id'] == first['turn_id']
    assert len(counts) == 1

def test_parallel_arrivals_and_restart_reuse_one_dispatch(stores):
    owner = stores[0]
    with ThreadPoolExecutor(max_workers=5) as pool:
        views = list(pool.map(lambda _: ensure(owner), range(5)))
    assert len({v['turn_id'] for v in views}) == 1
    clear_engine_cache()
    assert ensure(owner)['turn_id'] == views[0]['turn_id']

def test_ready_result_uses_actual_artifact_and_adoption_does_not_rerun(stores):
    owner = stores[0]
    view = ensure(owner)
    complete(owner, view)
    with acting(owner):
        ready = daily.read_daily_proposal(now=NOW)
        assert ready['status'] == 'ready'
        assert ready['artifact']['id'].startswith('daily-run-')
        assert ready['title'] == '自习散场，留住这一束灯'
        assert daily.daily_action(view['id'], action='adopt', now=NOW)['adopted'] is True
        assert daily.ensure_daily_proposal(refresh=True, now=NOW)['turn_id'] == view['turn_id']

def test_cross_tenant_and_store_cannot_adopt_private_daily_card(stores):
    owner, other = stores
    view = ensure(owner)
    complete(owner, view)
    with acting(other), pytest.raises(HTTPException) as exc:
        daily.daily_action(view['id'], action='adopt', now=NOW)
    assert exc.value.status_code == 404
    changed = dict(owner)
    raw = new_session(owner['context'].user_id, owner['context'].tenant_id, owner['other_store']['id'])
    changed['context'] = context_from_session(session_row(raw))
    with acting(changed), pytest.raises(HTTPException) as exc:
        daily.daily_action(view['id'], action='adopt', now=NOW)
    assert exc.value.status_code == 404

def test_profile_and_official_catalog_change_invalidate_draft(stores, monkeypatch):
    owner = stores[0]
    view = ensure(owner)
    complete(owner, view)
    with acting(owner):
        body = load_body('store', owner['store']['id'])
        save_body('store', owner['store']['id'], {**body, 'location': '杭州另一处同名广场'})
        assert daily.read_daily_proposal(now=NOW)['status'] == 'stale'
        with pytest.raises(HTTPException):
            daily.daily_action(view['id'], action='adopt', now=NOW)
        refreshed = daily.ensure_daily_proposal(now=NOW)
        assert refreshed['turn_id'] != view['turn_id']
    complete(owner, refreshed)
    monkeypatch.setattr(daily, '_catalog_revision', lambda: 'new-verified-official-knowledge')
    with acting(owner):
        assert daily.read_daily_proposal(now=NOW)['status'] == 'stale'

def test_changed_profile_does_not_duplicate_running_agent(stores):
    owner = stores[0]
    view = ensure(owner)
    with acting(owner):
        body = load_body('store', owner['store']['id'])
        save_body('store', owner['store']['id'], {**body, 'location': '更新后的真实地址'})
        again = daily.ensure_daily_proposal(now=NOW)
        assert again['turn_id'] == view['turn_id']
        assert again['status'] == 'stale' and again['in_progress']

def test_expiry_and_china_midnight_are_not_utc_day(stores):
    owner = stores[0]
    start = datetime(2026, 10, 24, 15, 59, tzinfo=timezone.utc).timestamp()
    with acting(owner):
        first = daily.ensure_daily_proposal(now=start)
        assert first['day'] == '2026-10-24'
        assert first['expires_at'] == start + 60
        second = daily.ensure_daily_proposal(now=start + 120)
        assert second['turn_id'] == first['turn_id'] and second['status'] == 'stale'
    complete(owner, first)
    with acting(owner):
        second = daily.ensure_daily_proposal(now=start + 120)
        assert second['day'] == '2026-10-25'
        assert second['turn_id'] != first['turn_id']

def test_failure_and_dismissal_require_explicit_action(stores):
    owner = stores[0]
    view = ensure(owner)
    complete(owner, view, status='failed')
    assert ensure(owner)['turn_id'] == view['turn_id']
    retry = ensure(owner, refresh=True)
    assert retry['turn_id'] != view['turn_id']
    complete(owner, retry)
    with acting(owner):
        assert daily.daily_action(retry['id'], action='dismiss', now=NOW)['status'] == 'dismissed'
        assert daily.ensure_daily_proposal(now=NOW)['status'] == 'dismissed'

def test_enqueue_failure_rolls_back_conversation_and_allows_recovery(stores):
    owner = stores[0]
    def fail(*_, **__):
        raise RuntimeError('transient queue failure')
    with pytest.raises(RuntimeError):
        ensure(owner, enqueue=fail)
    with acting(owner):
        assert daily.read_daily_proposal(now=NOW)['status'] == 'idle'
    assert ensure(owner)['status'] == 'preparing'

def test_routes_keep_anonymous_and_post_origin_guards(stores):
    application = FastAPI()
    install_tenancy(application)
    application.include_router(router)
    with TestClient(application) as client:
        assert client.get('/api/store/daily').status_code == 401
        client.cookies.set(COOKIE_NAME, stores[0]['raw'])
        assert client.post('/api/store/daily/prepare', json={}).status_code == 403
        response = client.post('/api/store/daily/prepare', json={}, headers={'Origin': 'http://testserver'})
        assert response.status_code == 202, response.text
        assert response.json()['status'] == 'preparing'

def test_injected_real_tools_execute_once_and_proactive_text_is_not_user_memory(stores):
    owner = stores[0]
    view = ensure(owner)
    class Catalog:
        calls = 0
        def catalog(self):
            self.calls += 1
            return [{'name': 'HUAWEI MateBook Pro'}]
    provider = Catalog()
    decisions = [
        {'action': 'tool', 'tool': 'read_catalog', 'arguments': {}},
        {'action': 'clarify', 'message': '今天是否主推这款？', 'questions': [
            {'field': 'product', 'label': '今天是否主推这款？', 'options': [
                {'value': 'HUAWEI MateBook Pro', 'label': 'MateBook Pro'}]}]},
    ]
    calls = []
    def planner(system, payload):
        calls.append(payload)
        return decisions[len(calls)-1]
    asyncio.run(jobs.execute_claimed(jobs.claim_next(), planner=planner,
        tools_factory=lambda c, t: AgentTools(c, t, provider=provider)))
    with acting(owner):
        proposal = daily.read_daily_proposal(now=NOW)
        assert proposal['status'] == 'needs_input'
        assert list_resources('work_signal') == []
        for _ in range(3):
            assert daily.ensure_daily_proposal(now=NOW)['turn_id'] == view['turn_id']
        assert provider.calls == 1 and len(calls) == 2
        followup = jobs.enqueue_turn(view['conversation_id'], TurnInput(
            text='今天顾客问到资料跨设备传输，请围绕这件事写。', client_turn_id=uuid4().hex))
        assert load_body(TURN, followup['id'])['input_origin'] == 'user'
        assert len(list_resources('work_signal')) == 1

def test_daily_policy_blocks_image_spend_and_fake_preference_write(stores):
    owner = stores[0]
    view = ensure(owner)
    with acting(owner):
        tools = AgentTools(get_conversation(view['conversation_id']), load_body(TURN, view['turn_id']))
        for name in ('create_design_bundle', 'create_poster', 'revise_design', 'remember'):
            with pytest.raises(HTTPException) as exc:
                asyncio.run(tools.execute(name, {}))
            assert exc.value.status_code == 422
