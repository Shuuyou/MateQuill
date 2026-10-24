"""Continuation restores scoped direction and freshly reads product facts."""
import asyncio
import time
from copy import deepcopy

import pytest

from app.gtm_agent.tools import AgentTools
from app.gtm_agent.repository import create_conversation
from app.tenancy import register_resource
from test_gtm_agent_core import identities, acting, Provider

pytestmark = pytest.mark.real_tenancy


def cached(owner):
    profile = {'name': owner['store']['name'], 'location': owner['store'].get('location', '')}
    research = {'query': '实际地址', 'state': 'found', 'profile': profile, 'items': [
        {'source_id': 'place1', 'title': '附近学校', 'url': 'https://example.com/location',
         'excerpt': '附近有校园', 'observed_at': '2026-10-24T00:00:00+00:00'}]}
    brief = {'location_status': 'resolved', 'location_summary': '实际地址', 'audience': '学生',
             'scene': '整理课堂笔记', 'visual_direction': '笔记与电脑', 'evidence_ids': ['place1']}
    return {'store_id': owner['store']['id'], 'saved_at': time.time(), 'profile': profile,
            'researches': [research], 'creative_brief': brief}


def test_continuation_reuses_source_bound_grounding(identities):
    owner = identities[0]
    with acting(owner):
        conversation = create_conversation()
        conversation['store_research'] = cached(owner)
        tools = AgentTools(conversation, {'id': 'new', 'text': '继续完成刚才的文案'})
        assert tools.place_selection.scene == '整理课堂笔记'
        assert not tools.context_summary()['requires_fresh_research']


@pytest.mark.parametrize('change', ['new_location', 'expired', 'store', 'profile', 'new_goal'])
def test_changed_scope_or_new_goal_does_not_restore_direction(identities, change):
    owner = identities[0]
    with acting(owner):
        conversation = create_conversation()
        snapshot = cached(owner)
        text = '继续完成刚才的文案'
        if change == 'new_location': text = '继续给新的海棠广场店写一篇'
        if change == 'new_goal': text = '另写一篇面向上班族的内容'
        if change == 'expired': snapshot['saved_at'] -= 86401
        if change == 'store': snapshot['store_id'] = 'different-store'
        if change == 'profile': snapshot['profile']['location'] = '搬迁前地址'
        conversation['store_research'] = snapshot
        tools = AgentTools(conversation, {'id': 'new', 'text': text})
        assert tools.place_selection is None


def test_product_restoration_retrieves_current_bundle_not_history_text(identities):
    owner = identities[0]
    class FreshProvider(Provider):
        def __init__(self): self.calls = []
        def retrieve(self, product, query):
            self.calls.append(product)
            bundle = super().retrieve(product, query)
            bundle.facts[0].value = '当前来源中的能力'
            return bundle
    with acting(owner):
        conversation = create_conversation()
        turn = {'id': 'resumed', 'text': '继续', 'observations': [
            {'tool': 'read_product', 'result': {'product': 'HUAWEI MateBook Pro',
                                              'facts': [{'value': '旧的错误表述'}]}}]}
        provider = FreshProvider()
        tools = AgentTools(conversation, turn, provider=provider)
        observations = asyncio.run(tools.restore_context())
        assert provider.calls == ['HUAWEI MateBook Pro']
        assert observations[0]['result']['facts'][0]['value'] == '当前来源中的能力'
        assert tools.context_summary()['ready_products'] == ['HUAWEI MateBook Pro']


def test_legacy_grounding_restores_only_from_same_authorized_conversation(identities):
    owner = identities[0]
    with acting(owner):
        conversation = create_conversation()
        snapshot = cached(owner)
        brief = snapshot.pop('creative_brief')
        register_resource('agent_turn', 'old-turn', body={'conversation_id': conversation['id'],
            'observations': [{'tool': 'ground_store', 'result': {'creative_brief': brief}}]})
        conversation['store_research'] = snapshot
        conversation['messages'] = [{'id': 'old-turn:assistant', 'role': 'assistant'}]
        tools = AgentTools(conversation, {'id': 'new', 'text': '继续'})
        assert tools.place_selection.scene == brief['scene']
        foreign = deepcopy(conversation)
        foreign['id'] = 'another-conversation'
        tools = AgentTools(foreign, {'id': 'new', 'text': '继续'})
        assert tools.place_selection is None
