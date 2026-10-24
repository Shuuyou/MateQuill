"""Same-store geography reuse saves a network request, never a product read."""
import asyncio

import pytest

from app.gtm_agent.research_memory import recover_own_research
from app.gtm_agent.tools import AgentTools
from test_gtm_agent_core import identities, acting, start
from test_research_memory import save_research, QUERY

pytestmark = pytest.mark.real_tenancy
PROFILE = {'name': '海棠体验店', 'location': '某市体验街12号'}


def cached(*, query=QUERY, profile=None, **kwargs):
    return recover_own_research(query, profile=profile or PROFILE, discovery={},
        reuse_before_search=True, **kwargs)


def test_proactive_reuse_preserves_age_quotes_and_discloses_no_new_network(identities):
    with acting(identities[0]):
        identifier, original = save_research(profile=PROFILE)
        result = cached()
        assert result['items'] == original['items']
        assert result['observed_at'] == original['observed_at']
        assert result['network_state'] == 'not_requested'
        assert result['cache_policy'] == 'same_owner_store_profile_exact_query_24h'
        assert result['prior_conversation_id'] == identifier
        assert 'fresh_attempt_failed' not in result


@pytest.mark.parametrize('case', ['expired', 'future', 'query', 'location', 'name', 'incomplete'])
def test_proactive_reuse_rejects_stale_and_nonidentical_place(identities, case):
    with acting(identities[0]):
        save_research(profile=PROFILE, age=86401 if case == 'expired' else -60 if case == 'future' else 60)
        profile = dict(PROFILE)
        if case in {'location', 'name'}:
            profile[case] = '另一个地点'
        if case == 'incomplete':
            profile['location'] = ''
        assert cached(profile=profile, query='另一个广场' if case == 'query' else QUERY) is None


def test_proactive_reuse_never_crosses_owner_or_group_visibility(identities):
    with acting(identities[0]):
        save_research(profile=PROFILE)
        assert cached(audience='group') is None
    with acting(identities[1]):
        assert cached() is None


@pytest.mark.parametrize('text', ['帮我写一篇本店的朋友圈内容', '为最新新品写一篇推广内容'])
def test_tool_can_reuse_prior_exact_query_without_search_or_old_creative_choice(identities, text):
    owner = identities[0]
    conversation, turn = start(owner, text)
    with acting(owner):
        save_research(profile=PROFILE)
        async def forbidden(query):
            pytest.fail('An exact fresh own-store query should reuse the actual observations')
        tools = AgentTools(conversation, {**turn, 'text': text}, search=forbidden)
        tools.store_profile = lambda: dict(PROFILE)
        result = asyncio.run(tools.execute('research_store', {'query': QUERY}))
        assert result['network_state'] == 'not_requested'
        assert result['sources'][0]['source_id'] == 'prior-real-source'
        assert tools.place_selection is None
        assert tools._researched_this_turn


@pytest.mark.parametrize('text', ['帮我重新查一下门店周边', '请实时调研后写内容', '给新的银杉广场店写朋友圈'])
def test_explicit_new_place_and_refresh_requests_still_search(identities, text):
    owner = identities[0]
    conversation, turn = start(owner, text)
    with acting(owner):
        save_research(profile=PROFILE)
        calls = []
        async def search(query):
            calls.append(query)
            return []
        tools = AgentTools(conversation, {**turn, 'text': text}, search=search)
        tools.store_profile = lambda: dict(PROFILE)
        result = asyncio.run(tools.execute('research_store', {'query': QUERY}))
        assert calls == [QUERY]
        assert result['state'] == 'no_results'
        assert not result.get('cache_hit')


def test_requested_live_refresh_cannot_fall_back_to_cached_result(monkeypatch, identities):
    owner = identities[0]
    conversation, turn = start(owner, '请实时查询门店周边')
    async def unavailable(*args, **kwargs):
        return {'network_state': 'unavailable', 'state': 'no_results', 'items': []}
    monkeypatch.setattr('app.place_discovery.discover_place', unavailable)
    with acting(owner):
        save_research(profile=PROFILE)
        tools = AgentTools(conversation, {**turn, 'text': '请实时查询门店周边'})
        tools.store_profile = lambda: dict(PROFILE)
        result = asyncio.run(tools.execute('research_store', {'query': QUERY}))
        assert result['network_state'] == 'unavailable'
        assert result['state'] == 'no_results'
        assert not result.get('cache_hit')
