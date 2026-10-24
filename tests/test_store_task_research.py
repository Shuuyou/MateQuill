"""Legacy job entry points share place evidence without overwriting user intent."""
import asyncio
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from app import store_task_research as module
from app.gtm_agent.effects import _claim
from app.models import TaskInput


@pytest.fixture
def workspace(monkeypatch):
    context = SimpleNamespace(store_id='store-a', is_legacy=False)
    monkeypatch.setattr(module, 'get_context', lambda: context)
    monkeypatch.setattr(module, 'authorize_resource', lambda kind, identifier: {
        'body': {'name': '海棠广场店', 'location': '海棠市大学路'}})
    return context


def observation(state='found'):
    return {'state': state, 'observed_at': '2026-10-22T12:00:00+08:00', 'items': [{
        'source_id': 'place:test', 'title': '海棠广场位置', 'url': 'https://public.example/place',
        'provider': 'test', 'excerpt': '广场旁有大学生活区。', 'observed_at': '2026-10-22T12:00:00+08:00'}]}


def test_job_preserves_explicit_audience_and_includes_real_research(workspace):
    task = TaskInput(product='手机', store_id='store-a', audience='教师', scene='课后整理资料')
    seen = []
    async def discover(query, *, profile):
        seen.append((query, profile))
        return observation()
    def planner(system, payload):
        return {'location_status': 'resolved', 'location_summary': '大学路海棠广场',
                'audience': '教师', 'scene': '课后整理资料', 'visual_direction': '书桌上的工作资料',
                'evidence_ids': ['place:test']}
    result = asyncio.run(module.prepare_store_task(task, discover=discover, planner=planner))
    assert result is not task
    assert result.audience == '教师' and result.scene == '课后整理资料'
    assert 'https://public.example/place' in result.requirements
    assert '书桌上的工作资料' in result.requirements
    assert seen[0][1]['location'] == '海棠市大学路'
    assert not task.requirements


def test_job_rejects_ambiguous_location_before_generation(workspace):
    with pytest.raises(HTTPException) as exc:
        asyncio.run(module.prepare_store_task(TaskInput(), discover=lambda *a, **k: observation('ambiguous')))
    assert exc.value.status_code == 422


def test_job_cannot_move_research_to_another_store(workspace):
    with pytest.raises(HTTPException) as exc:
        asyncio.run(module.prepare_store_task(TaskInput(store_id='store-b')))
    assert exc.value.status_code == 409


def test_gtm_owned_generation_does_not_repeat_place_research(workspace):
    token = _claim.set(('turn', 'lease'))
    try:
        def forbidden(*a, **k):
            raise AssertionError('GTM has already prepared source-bound research')
        task = TaskInput()
        assert asyncio.run(module.prepare_store_task(task, discover=forbidden)) is task
    finally:
        _claim.reset(token)


def test_channel_reuses_only_exact_server_preparation(workspace, monkeypatch):
    from app import channel_workflows, store_autopilot, tenancy
    task = TaskInput(product='手机', store_id='store-a', assignment_id='assigned', requirements='正式活动\n真实本店选题')
    checked = []
    monkeypatch.setattr(channel_workflows, 'validate_generation_task', lambda value: checked.append(value))
    monkeypatch.setattr(channel_workflows, '_assignment', lambda identifier: ({}, {'id': 'assigned'}, {'version': 2}))
    monkeypatch.setattr(store_autopilot, 'opportunity_cache_key', lambda *a: 'scoped-cache')
    cached = {'assignment_id':'assigned', 'campaign_version':2, 'result':{'status':'ready','task':task.model_dump()}}
    monkeypatch.setattr(tenancy, 'load_body', lambda kind, identifier: cached)
    def forbidden(*args, **kwargs):
        raise AssertionError('Exact source-bound preparation must not research twice')
    assert asyncio.run(module.prepare_store_task(task, discover=forbidden)) is task
    assert checked == [task]
    changed = task.model_copy(update={'requirements':task.requirements+'\n浏览器新加标记'})
    assert not module.prepared_channel_task(changed)
    cached['campaign_version'] = 1
    assert not module.prepared_channel_task(task)


def test_internal_display_suffix_does_not_hide_actual_building(workspace, monkeypatch):
    from app.place_discovery import parse_search_html, query_entities
    body = {'name': '杭州百脑汇华为店 · 演示门店', 'location': '杭州市西湖区教工路23号百脑汇1D-03'}
    original = dict(body)
    monkeypatch.setattr(module, 'authorize_resource', lambda *args: {'body': body})
    seen = []
    async def discover(query, *, profile):
        seen.append((query, profile))
        html = '<li class="res-list"><h3><a href="https://example.org/building">杭州百脑汇</a></h3><p>地址：教工路23号，附近有商业办公设施。</p></li>'
        rows = parse_search_html(html, query=query, provider='so_public_search', observed_at='2026-10-23')
        return {'state': 'found' if rows else 'no_results', 'items': rows}
    def planner(system, payload):
        assert payload['store_profile']['name'] == original['name']
        return {'location_status': 'resolved', 'audience': '通勤人群', 'scene': '下班到店体验',
                'evidence_ids': [payload['research']['items'][0]['source_id']]}
    result = asyncio.run(module.prepare_store_task(TaskInput(), discover=discover, planner=planner))
    assert len(seen) == 1
    assert query_entities(seen[0][0]) == ['教工路23号']
    assert '演示门店' not in seen[0][0] and '1D-03' not in seen[0][0]
    assert seen[0][1]['name'] == '杭州百脑汇华为店'
    assert 'https://example.org/building' in result.requirements
    assert body == original


def test_no_results_uses_only_one_safe_public_name_fallback(workspace, monkeypatch):
    seen = []
    async def discover(query, *, profile):
        seen.append(query)
        return {'state': 'no_results', 'items': []}
    asyncio.run(module.prepare_store_task(TaskInput(), discover=discover))
    # This profile has no street number, so there is only one independent
    # public anchor rather than a chain of increasingly speculative POIs.
    assert len(seen) == 1
    queries, _ = module.public_place_queries('杭州百脑汇华为店 · 演示门店', '杭州市西湖区教工路23号百脑汇1D-03')
    # Inspecting both generated queries also verifies the alternate changes
    # the parser anchor instead of simply repeating the failed query.
    from app.place_discovery import query_entities
    assert len(queries) == 2
    assert query_entities(queries[0]) == ['教工路23号']
    assert query_entities(queries[1]) == ['杭州百脑汇华为店']
    monkeypatch.setattr(module, 'authorize_resource', lambda *args: {'body': {
        'name': '杭州百脑汇华为店 · 演示门店', 'location': '杭州市西湖区教工路23号百脑汇1D-03'}})
    seen.clear()
    result = asyncio.run(module.prepare_store_task(TaskInput(), discover=discover))
    assert seen == queries
    assert 'public.example' not in result.requirements


def test_fallback_ambiguity_is_never_silently_overridden(workspace, monkeypatch):
    monkeypatch.setattr(module, 'authorize_resource', lambda *args: {'body': {
        'name': '公开门店 · 测试账号', 'location': '海棠市大学路12号二楼'}})
    queries = []
    async def discover(query, *, profile):
        queries.append(query)
        return {'state': 'no_results' if len(queries) == 1 else 'ambiguous', 'items': []}
    with pytest.raises(HTTPException) as exc:
        asyncio.run(module.prepare_store_task(TaskInput(), discover=discover))
    assert exc.value.status_code == 422 and len(queries) == 2


@pytest.mark.parametrize('name', ['华为演示技术体验店', '测试仪器商店', '杭州测试中心店'])
def test_real_names_containing_label_words_are_not_stripped(name):
    queries, profile = module.public_place_queries(name, '')
    assert profile['name'] == name and name in queries[0]
