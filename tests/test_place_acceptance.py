"""Acceptance boundaries: contract tests are separate from live blind discovery."""
from copy import deepcopy
from contextlib import contextmanager
from uuid import uuid4

import asyncio
import pytest
from fastapi import HTTPException

from scripts.verify_place_research import assess_discovery

pytestmark = pytest.mark.real_tenancy


def evidence_result():
    return {'status': 'found', 'query': '海棠广场 城市 周边', 'cache_hit': False,
            'providers': [{'provider': 'test_search', 'status': 'found', 'count': 1}],
            'items': [{'source_id': 'public-source-1', 'provider': 'test_search',
                       'url': 'https://public.example/place', 'title': '海棠广场周边交通',
                       'excerpt': '广场附近是大学学生生活区。', 'observed_at': '2026-10-22T12:00:00+08:00'}]}


def test_live_acceptance_rejects_no_results_or_model_only_claims():
    for status in ('no_results', 'unavailable', 'found'):
        result = {'status': status, 'cache_hit': False, 'items': [],
                  'audience': '大学生', 'providers': [{'status': 'unavailable'}]}
        assert not assess_discovery(result)['passed']


def test_cold_start_acceptance_does_not_count_cached_result_as_live():
    result = evidence_result()
    assert assess_discovery(result, require_tokens=['大学'])['passed']
    result['cache_hit'] = True
    assert not assess_discovery(result)['passed']
    assert assess_discovery(result, allow_cache=True)['passed']


def test_source_must_have_public_url_provider_and_observation_date():
    original = evidence_result()
    for key, value in [('url', 'http://127.0.0.1/private'), ('provider', ''),
                       ('observed_at', ''), ('observed_at', '2026-10-22'), ('source_id', ''), ('excerpt', '')]:
        result = deepcopy(original)
        result['items'][0][key] = value
        assert not assess_discovery(result)['passed'], key


def test_blind_case_expected_context_must_be_in_actual_sources():
    campus = evidence_result()
    assert assess_discovery(campus, require_tokens=['大学'])['passed']
    assert not assess_discovery(campus, require_tokens=['写字楼'])['passed']
    office = deepcopy(campus)
    office['items'][0]['excerpt'] = '广场连接办公园区与写字楼通勤入口。'
    assert assess_discovery(office, require_tokens=['写字楼'])['passed']
    assert not assess_discovery(office, require_tokens=['大学'])['passed']


def test_no_cache_status_must_be_explicit():
    result = evidence_result()
    del result['cache_hit']
    assert not assess_discovery(result)['passed']


def test_research_without_selection_preserves_evidence_without_inventing_audience():
    from app.gtm_agent.place_context import build_place_context
    research = evidence_result()
    context = build_place_context([research], user_text='给这家店写一条朋友圈')
    assert context['sources']
    assert not context.get('audience_hypotheses')
    assert not context.get('creative_brief')
    assert context['user_direction'] == '给这家店写一条朋友圈'


def test_selection_cannot_cite_unobserved_evidence_or_resolve_without_sources():
    from app.gtm_agent.place_context import validate_place_selection
    selection = {'location_status': 'resolved', 'location_summary': '大学路的海棠广场',
                 'audience': '附近学习生活的人', 'scene': '课程结束后整理资料',
                 'visual_direction': '真实桌面与课程资料', 'evidence_ids': ['fabricated-source']}
    with pytest.raises(ValueError):
        validate_place_selection(selection, [evidence_result()])
    selection['evidence_ids'] = []
    with pytest.raises(ValueError):
        validate_place_selection(selection, [])


def test_private_url_cannot_be_used_as_location_evidence():
    from app.gtm_agent.place_context import build_place_context, validate_place_selection
    research = evidence_result()
    research['items'][0]['url'] = 'http://127.0.0.1/internal'
    context = build_place_context([research])
    assert not context['sources']
    assert context['state'] == 'no_results'
    selection = {'location_status': 'resolved', 'location_summary': '研究所得地址',
                 'audience': '附近居民', 'scene': '日常生活', 'visual_direction': '',
                 'evidence_ids': ['public-source-1']}
    with pytest.raises(ValueError):
        validate_place_selection(selection, [research])


def test_same_name_ambiguity_stays_ambiguous_until_agent_resolves_it():
    from app.gtm_agent.place_context import build_place_context
    research = evidence_result()
    research['items'].append({**research['items'][0], 'source_id': 'public-source-2',
                              'url': 'https://public.example/another-city',
                              'excerpt': '另一城市也有海棠广场，位于高新办公园区。'})
    selection = {'location_status': 'ambiguous', 'location_summary': '存在不同城市的同名地点',
                 'audience': '', 'scene': '', 'visual_direction': '',
                 'evidence_ids': ['public-source-1', 'public-source-2'],
                 'alternatives': ['大学路海棠广场', '高新区海棠广场']}
    context = build_place_context([research], selection=selection)
    assert context['state'] == 'ambiguous'


def test_new_user_correction_is_separate_from_previous_editorial_selection():
    from app.gtm_agent.place_context import build_place_context
    research = evidence_result()
    correction = '不是大学路那家，是高新区办公楼旁的同名店，这次面向通勤人群。'
    selection = {'location_status': 'resolved', 'location_summary': '大学路海棠广场',
                 'audience': '附近学习生活的人', 'scene': '课程结束后整理资料',
                 'visual_direction': '真实桌面与课程资料', 'evidence_ids': ['public-source-1']}
    context = build_place_context([research], user_text=correction, selection=selection)
    assert context['user_direction'] == correction
    assert context['sources'][0]['excerpt'] == research['items'][0]['excerpt']


@pytest.fixture
def place_tenants(tmp_path, monkeypatch):
    from app.store_campaigns import StoreInput, StoreRepository
    from app.tenancy import set_context, reset_context
    from app.tenancy.auth import new_session, context_from_session, session_row
    from app.tenancy.database import clear_engine_cache
    from scripts.tenant_admin import bootstrap
    monkeypatch.setenv('MATEQUILL_DATABASE_URL', 'sqlite:///' + (tmp_path / 'place.sqlite').as_posix())
    clear_engine_cache()
    owners = []
    for suffix in ('a', 'b'):
        account = bootstrap(email=f'place-{uuid4().hex}@example.test', name='门店运营',
                            tenant_name='Place tenant ' + suffix, password='Place-Tests-Only-2026-Strong!')
        token = set_context(context_from_session(session_row(new_session(account['user_id'], account['tenant_id']))))
        try:
            store = StoreRepository().save_store(StoreInput(name='海棠广场店', location='城市' + suffix))
        finally:
            reset_context(token)
        raw = new_session(account['user_id'], account['tenant_id'], store['id'])
        owners.append(context_from_session(session_row(raw)))
    yield owners
    clear_engine_cache()


@contextmanager
def place_actor(context):
    from app.tenancy import set_context, reset_context
    token = set_context(context)
    try:
        yield
    finally:
        reset_context(token)


def test_research_persists_across_turns_without_cross_tenant_leakage(place_tenants):
    from app.gtm_agent.tools import AgentTools
    from app.gtm_agent.repository import create_conversation, get_conversation
    first, second = place_tenants
    with place_actor(first):
        conversation = create_conversation()
        tools = AgentTools(conversation, {'id': 'research-turn-a', 'text': '研究周边'},
                           search=lambda _: evidence_result()['items'])
        asyncio.run(tools.execute('research_store', {'query': '"海棠广场" 城市a 周边'}))
        restored = AgentTools(get_conversation(conversation['id']), {'id': 'new-turn-a', 'text': '接着创作'})
        assert restored.store_research
    with place_actor(second):
        with pytest.raises(HTTPException) as forbidden:
            get_conversation(conversation['id'])
        assert forbidden.value.status_code == 404
        other_conversation = create_conversation()
        other = AgentTools(other_conversation, {'id': 'other-turn', 'text': '研究周边'})
        assert not other.store_research
