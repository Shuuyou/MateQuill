"""A failed network attempt may reuse only this employee's actual recent research."""
import asyncio
from datetime import datetime, timezone
import time

import pytest
from sqlalchemy import update

from app.gtm_agent.research_memory import recover_own_research
from app.gtm_agent.repository import CONVERSATION, create_conversation, conversation_row
from app.gtm_agent.tools import AgentTools
from app.store_campaigns import StoreInput, StoreRepository
from app.tenancy import service_update_body
from app.tenancy.database import db_connection, resources
from test_gtm_agent_core import identities, acting, start

pytestmark = pytest.mark.real_tenancy
QUERY = '"海棠广场" 地址 周边'


def save_research(*, age=60, query=QUERY, profile=None, store_id=None, audience='private'):
    conversation = create_conversation(audience=audience)
    row = conversation_row(conversation['id'])
    research = {'query': query, 'profile': profile or {}, 'state':'found',
        'observed_at':datetime.fromtimestamp(time.time()-age, timezone.utc).isoformat(),
        'items':[{'source_id':'prior-real-source','title':'学校地址说明',
            'url':'https://public.example/school','excerpt':'周边学校的公开位置',
            'provider':'public_search','observed_at':'2026-10-22T18:00:00+00:00'}]}
    service_update_body(CONVERSATION, row['id'], {**row['body'],'store_research': {
        'store_id': store_id or row['store_id'], 'saved_at':time.time(), 'researches':[research]}}, expected_version=row['version'])
    return conversation['id'], research


def recover(**kwargs):
    return recover_own_research(QUERY, discovery={'network_state':'unavailable','items':[]}, profile={}, **kwargs)


def test_own_real_research_preserves_original_source_and_age(identities):
    with acting(identities[0]):
        identifier, original = save_research()
        recovered = recover()
        assert recovered['items'] == original['items']
        assert recovered['observed_at'] == original['observed_at']
        assert recovered['prior_conversation_id'] == identifier
        assert recovered['cache_hit'] is True
        assert recovered['fresh_attempt_failed'] is True
        assert recovered['research_origin'] == 'own_prior_research'
        recovered['items'][0]['title'] = 'mutation'
        assert recover()['items'][0]['title'] != 'mutation'


@pytest.mark.parametrize('age', [86401, -60])
def test_stale_or_future_research_is_not_reused(identities, age):
    with acting(identities[0]):
        save_research(age=age)
        assert recover() is None


@pytest.mark.parametrize('change', ['query','location','owner','store','tenant','empty_search'])
def test_unrelated_scope_or_successful_empty_search_cannot_fill_result(identities, change):
    owner = identities[0]
    with acting(owner):
        identifier, _ = save_research(query='另一广场 地址' if change == 'query' else QUERY,
            profile={'location':'另一条道路'} if change == 'location' else {})
        if change == 'owner':
            with db_connection() as connection:
                connection.execute(update(resources).where(resources.c.id == identifier).values(
                    owner_id=identities[1]['context'].user_id))
        if change == 'store':
            other = StoreRepository().save_store(StoreInput(name='另一门店'))
            with db_connection() as connection:
                connection.execute(update(resources).where(resources.c.id == identifier).values(store_id=other['id']))
        if change == 'empty_search':
            assert recover_own_research(QUERY, discovery={'network_state':'available','items':[]}, profile={}) is None
        elif change != 'tenant':
            assert recover() is None
    if change == 'tenant':
        with acting(identities[1]):
            assert recover() is None


def test_research_tool_exposes_failed_fresh_attempt_and_own_source(monkeypatch, identities):
    async def unavailable(*args, **kwargs):
        return {'network_state':'unavailable','state':'no_results','items':[],
            'observed_at':datetime.now(timezone.utc).isoformat()}
    monkeypatch.setattr('app.place_discovery.discover_place', unavailable)
    owner = identities[0]
    conversation, turn = start(owner, '研究海棠广场店')
    with acting(owner):
        tools = AgentTools(conversation, {**turn,'text':'研究海棠广场店'})
        _, original = save_research(profile=tools.store_profile())
        result = asyncio.run(tools.execute('research_store', {'query':QUERY}))
        assert result['state'] == 'found'
        assert result['network_state'] == 'unavailable'
        assert result['cache_hit'] and result['fresh_attempt_failed']
        assert result['observed_at'] == original['observed_at']
        assert result['sources'][0]['source_id'] == 'prior-real-source'


def test_group_recovery_never_promotes_own_private_research(identities):
    with acting(identities[0]):
        save_research()
        assert recover(audience='group') is None
        assert recover(audience='private') is not None


def test_group_recovery_accepts_own_store_visible_group_research(identities):
    with acting(identities[0]):
        identifier, original = save_research(audience='group')
        result = recover(audience='group')
        assert result['prior_conversation_id'] == identifier
        assert result['items'] == original['items']
