import asyncio
from copy import deepcopy

from fastapi import HTTPException
import pytest

from app.store_opportunity_agent import plan_store_opportunity


@pytest.fixture
def opportunity_context(monkeypatch, tmp_path):
    from app.tenancy import TenantContext, set_context, reset_context, register_resource
    from app.tenancy.database import db_connection, tenants, users
    monkeypatch.setenv('MATEQUILL_DATABASE_URL', 'sqlite:///' + (tmp_path / 'opportunity.sqlite3').as_posix())
    with db_connection() as conn:
        conn.execute(tenants.insert(), [{'id': 'tenant-a', 'name': 'A', 'created_at': 1}, {'id': 'tenant-b', 'name': 'B', 'created_at': 1}])
        conn.execute(users.insert(), {'id': 'u', 'email': 'u@test.local', 'name': 'U', 'password_hash': 'x', 'active': True, 'created_at': 1})
    token = set_context(TenantContext(user_id='u', tenant_id='tenant-a', store_id='store-a',
                                    roles=('salesperson',), store_ids=('store-a',)))
    task = {'product': 'HUAWEI MateBook', 'platform': 'xiaohongshu', 'store_id': 'store-a',
            'assignment_id': 'assignment-a', 'requirements': '{"campaign":{"title":"真实活动"}}',
            'goal': '让顾客了解设备如何协作', 'ecosystem_products': ['MatePad']}
    register_resource('store', 'store-a', 'store-a', 'store', {'name': '杭州体验店', 'location': '杭州文三路', 'audience': ''})
    register_resource('channel_campaign', 'campaign-a', 'store-a', 'store', {'id': 'campaign-a',
        'version': 1, 'status': 'active', 'title': '真实活动', 'product': task['product'], 'platform': task['platform'],
        'ecosystem_products': task['ecosystem_products'], 'source_refs': [{'url': 'https://consumer.huawei.com/cn/'}]})
    register_resource('channel_assignment', 'assignment-a', 'store-a', 'store', {'id': 'assignment-a',
        'campaign_id': 'campaign-a', 'campaign_version': 1, 'store_id': 'store-a', 'task': task})
    try:
        yield task, set_context, TenantContext
    finally:
        reset_context(token)


def opportunity(**changes):
    return {'title': '三台设备，一件事怎么接着做', 'summary': '以跨设备整理课堂材料作为内容方向',
            'audience': '需要整理课堂材料的人', 'audience_basis': 'editorial_hypothesis',
            'scene': '把课堂材料从手机整理到大屏设备', 'creative_direction': '先展示一个具体任务，再解释操作步骤',
            'visual_idea': '官方素材加任务步骤卡', 'reasons': ['活动指定了设备组合', '公开学校信息可以提供选题线索'],
            'evidence_ids': ['campaign:current', 'signal:1'], **changes}


@pytest.mark.real_tenancy
def test_agent_chooses_tools_and_locks_task_without_manual_profile(opportunity_context):
    original, _, _ = opportunity_context
    calls = []
    queries = []

    async def planner(system, payload):
        calls.append(deepcopy(payload))
        if len(calls) == 1:
            return {'action': 'tool', 'tool_calls': [{'tool': 'read_store'}, {'tool': 'read_campaign'}]}
        if len(calls) == 2:
            return {'action': 'tool', 'tool_calls': [{'tool': 'read_signals', 'query': '杭州文三路 高校 活动'}, {'tool': 'read_history'}]}
        return {'action': 'plan', 'opportunity': opportunity(audience_basis='store_record')}

    async def search(query):
        queries.append(query)
        return [{'title': '高校公开活动', 'url': 'https://example.edu.cn/activity', 'excerpt': '公开活动信息', 'observed_at': '2026-10-22T00:00:00Z'}]

    result = asyncio.run(plan_store_opportunity('assignment-a', planner=planner, search=search,
                                              instruction='换一个更像日常观察的角度'))
    assert result['state'] == 'ready' and len(calls) == 3
    assert queries == ['杭州体验店 杭州文三路 真实活动 高校 活动']
    assert result['opportunity']['audience_basis'] == 'editorial_hypothesis'
    assert result['task']['requirements'].startswith(original['requirements'] + '\n')
    for field in ('product', 'platform', 'store_id', 'assignment_id', 'ecosystem_products'):
        assert result['task'][field] == original[field]
    assert result['opportunity']['strategy_id']
    assert calls[0]['user_direction'] == '换一个更像日常观察的角度'
    assert {'store:profile', 'campaign:current', 'signal:1', 'store:history'} == {row['source_id'] for row in result['sources']}


@pytest.mark.real_tenancy
def test_unknown_tool_and_fabricated_evidence_cannot_execute(opportunity_context):
    calls = []

    async def planner(system, payload):
        calls.append(payload)
        if len(calls) == 1:
            return {'action': 'tool', 'tool_calls': [{'tool': 'http_get', 'query': 'http://internal/secret'}]}
        if len(calls) == 2:
            return {'action': 'tool', 'tool_calls': [{'tool': 'read_store'}, {'tool': 'read_campaign'}]}
        return {'action': 'plan', 'opportunity': opportunity(evidence_ids=['invented'])}

    async def no_search(query): raise AssertionError('Arbitrary HTTP must never run')
    result = asyncio.run(plan_store_opportunity('assignment-a', planner=planner, search=no_search))
    assert result['state'] == 'unavailable' and result['status'] == 'unavailable'
    assert result['reason_code'] == 'planning_budget_exhausted'
    assert len(calls) == 3


@pytest.mark.real_tenancy
def test_cross_tenant_and_wrong_active_store_rejected_before_model(opportunity_context):
    _, set_context, Context = opportunity_context
    async def planner(*args): raise AssertionError('Should authorize before contacting model')
    set_context(Context(user_id='u', tenant_id='tenant-b', store_id='store-a', roles=('salesperson',), store_ids=('store-a',)))
    with pytest.raises(HTTPException) as denied:
        asyncio.run(plan_store_opportunity('assignment-a', planner=planner, search=lambda _: []))
    assert denied.value.status_code == 404
    set_context(Context(user_id='u', tenant_id='tenant-a', store_id='store-b', roles=('admin',), all_stores=True))
    with pytest.raises(HTTPException) as wrong:
        asyncio.run(plan_store_opportunity('assignment-a', planner=planner, search=lambda _: []))
    assert wrong.value.status_code == 409


@pytest.mark.real_tenancy
def test_missing_model_reports_unavailable_without_fake_plan(opportunity_context, monkeypatch):
    from app.config import settings
    monkeypatch.setattr(settings, 'llm_api_key', '')
    result = asyncio.run(plan_store_opportunity('assignment-a', search=lambda _: []))
    assert result['state'] == 'unavailable'
    assert result['opportunity'] is None


@pytest.mark.real_tenancy
def test_queries_are_bounded_and_personal_contacts_not_sent(opportunity_context):
    attempts = []
    count = 0
    async def planner(system, payload):
        nonlocal count
        count += 1
        if count == 1:
            return {'action': 'tool', 'tool_calls': [{'tool': 'read_store'}, {'tool': 'read_campaign'}]}
        if count == 2:
            return {'action': 'tool', 'tool_calls': [{'tool': 'read_signals', 'query': '13800138000'}, {'tool': 'read_signals', 'query': 'https://internal/'}, {'tool': 'read_signals', 'query': '杭州新品活动'}]}
        return {'action': 'plan', 'opportunity': opportunity(evidence_ids=['campaign:current'])}
    async def search(query): attempts.append(query); return []
    result = asyncio.run(plan_store_opportunity('assignment-a', planner=planner, search=search))
    assert result['state'] == 'ready'
    assert attempts == ['杭州体验店 杭州文三路 真实活动 活动 新品']


@pytest.mark.real_tenancy
def test_history_stays_in_current_store_and_does_not_prove_actual_audience(opportunity_context):
    from app.tenancy import register_resource
    original, set_context, Context = opportunity_context
    set_context(Context(user_id='u', tenant_id='tenant-a', store_id='store-a', roles=('admin',), all_stores=True))
    register_resource('run', 'other-run', 'store-b', 'tenant', {'title': '另一门店私有选题'})
    register_resource('run', 'own-run', 'store-a', 'store', {'title': '本店旧选题', '_poster_task': {'audience': '目标人群假设'},
        'assets': [{'provenance': 'store_upload', 'title': '门店桌上的电脑和手写笔记', 'local_path': '/private/media/photo.png'}]})
    calls = []
    async def planner(system, payload):
        calls.append(deepcopy(payload))
        if len(calls) == 1:
            return {'action': 'tool', 'tool_calls': [{'tool': 'read_store'}, {'tool': 'read_campaign'}, {'tool': 'read_history'}]}
        return {'action': 'plan', 'opportunity': opportunity(evidence_ids=['campaign:current', 'store:history'])}
    result = asyncio.run(plan_store_opportunity('assignment-a', planner=planner, search=lambda _: []))
    assert result['state'] == 'ready'
    history = next(row for row in result['sources'] if row['source_id'] == 'store:history')
    assert [row['title'] for row in history['data']] == ['本店旧选题']
    assert history['data'][0]['input_context'] == ['门店桌上的电脑和手写笔记']
    assert '/private/media' not in str(history)
    assert result['opportunity']['audience_basis'] == 'editorial_hypothesis'


@pytest.mark.real_tenancy
def test_planner_failure_never_exposes_provider_secrets(opportunity_context):
    async def planner(*args): raise RuntimeError('Bearer secret should never appear')
    result = asyncio.run(plan_store_opportunity('assignment-a', planner=planner, search=lambda _: []))
    assert result['status'] == 'unavailable'
    assert result['reason_code'] == 'planner_unavailable'
    assert 'secret' not in str(result)
