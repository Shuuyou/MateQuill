"""Cold start parallelizes only known, read-only prerequisites; no invented briefs."""
import asyncio
from copy import deepcopy

import pytest
from fastapi import HTTPException

from app.gtm_agent.repository import create_conversation
from app.gtm_agent.tools import AgentTools, exact_requested_products
from app.models import EvidenceBundle, Fact, Source
from test_gtm_agent_core import identities, acting

pytestmark = pytest.mark.real_tenancy

CATALOG = [
    {'name': 'ALPHA Note Pro', 'aliases': ['甲牌笔记本专业版']},
    {'name': 'ALPHA Note Pro S', 'aliases': ['甲牌笔记本超轻版']},
    {'name': 'ALPHA Band 1', 'aliases': ['甲牌手环1']},
    {'name': 'ALPHA Band 10', 'aliases': ['甲牌手环10']},
]


def bundle(name):
    return EvidenceBundle(product=name, verified_local=True,
        facts=[Fact(id='f1', product=name, label='尺寸', value='14英寸', quote='14英寸', source_id='s1')],
        sources=[Source(id='s1', title='本次准确资料')])


@pytest.mark.parametrize('text,names', [
    ('给ALPHA Note Pro S写篇内容', ['ALPHA Note Pro S']),
    ('给甲牌手环10写朋友圈', ['ALPHA Band 10']),
    ('给ALPHABand10写朋友圈', ['ALPHA Band 10']),
    ('给ALPHA Band 100写朋友圈', []),
    ('给alpha note pro写篇内容', ['ALPHA Note Pro']),
    ('Create an ALPHA Band 10 post', ['ALPHA Band 10']),
    ('给甲牌笔记本超轻版和甲牌手环10写内容', ['ALPHA Note Pro S', 'ALPHA Band 10']),
    ('随便推荐一台电脑写内容', []),
])
def test_only_exact_catalog_names_or_aliases_are_preloaded(text, names):
    assert exact_requested_products(CATALOG, text) == names


def test_shared_alias_is_ambiguous_and_large_requests_stay_planner_directed():
    rows = [{'name': 'Product One', 'aliases': ['共享型号']}, {'name': 'Product Two', 'aliases': ['共享型号']}]
    assert exact_requested_products(rows, '给共享型号写朋友圈') == []
    assert exact_requested_products(CATALOG, ' '.join(row['name'] for row in CATALOG)) == []


def test_memory_instruction_does_not_start_product_or_location_fetches(identities):
    class Provider:
        def catalog(self): pytest.fail('A preference alone needs no product read')
    with acting(identities[0]):
        tools = AgentTools(create_conversation(), {'id': 'memory', 'text': '记住以后朋友圈写得自然一些'},
                           provider=Provider())
        assert asyncio.run(tools.bootstrap_context()) == []


def test_product_and_neutral_location_read_run_in_parallel_without_grounding(identities):
    owner = identities[0]
    with acting(owner):
        conversation = create_conversation()
        async def scenario():
            product_started, search_started = asyncio.Event(), asyncio.Event()
            class Provider:
                async def catalog(self): return deepcopy(CATALOG)
                async def retrieve(self, name, query):
                    product_started.set()
                    await search_started.wait()
                    return bundle(name)
            queries = []
            async def search(query):
                queries.append(query)
                search_started.set()
                await product_started.wait()
                return [{'source_id': 'place1', 'url': 'https://example.com/address',
                         'title': '实际地址', 'excerpt': '某市体验街12号，附近是图书馆。'}]
            tools = AgentTools(conversation, {'id': 'cold', 'text': '给甲牌手环10写朋友圈'},
                               provider=Provider(), search=search)
            tools.store_profile = lambda: {'name': '海棠体验店', 'location': '某市体验街12号'}
            observations = await asyncio.wait_for(tools.bootstrap_context(), timeout=2)
            assert {row['tool'] for row in observations} == {'read_catalog', 'read_product', 'research_store'}
            assert tools.context_summary()['ready_products'] == ['ALPHA Band 10']
            assert tools.place_selection is None and tools.opportunity is None
            assert '大学' not in queries[0] and '写字楼' not in queries[0]
            assert '某市体验街12号' in queries[0] and '地址 周边' in queries[0]
            assert tools.context_summary()['locality_cross_check_needed']
            with pytest.raises(HTTPException) as error:
                await tools.execute('ground_store', {'grounding': {
                    'location_status': 'resolved', 'location_summary': '某市体验街12号',
                    'audience': '读者', 'scene': '阅读', 'visual_direction': '图书馆外观', 'evidence_ids': ['place1']}})
            assert error.value.status_code == 422
            await tools.execute('research_store', {'query': '体验街12号 图书馆 地址'})
            await tools.execute('ground_store', {'grounding': {
                'location_status': 'resolved', 'location_summary': '两次实际地址查询',
                'audience': '读者', 'scene': '阅读', 'visual_direction': '图书馆外观', 'evidence_ids': ['place1']}})
            assert tools.place_selection.audience == '读者'
        asyncio.run(scenario())


def test_explicit_new_location_is_not_replaced_by_current_store_profile(identities):
    owner = identities[0]
    with acting(owner):
        conversation = create_conversation()
        class Provider:
            def catalog(self): return deepcopy(CATALOG)
            def retrieve(self, name, query): return bundle(name)
        async def forbidden_search(query):
            pytest.fail('A new explicit place must be researched from the request, not current-store profile')
        tools = AgentTools(conversation, {'id': 'new-place', 'text': '给新的银杉广场店写甲牌手环10朋友圈'},
                           provider=Provider(), search=forbidden_search)
        tools.store_profile = lambda: {'name': '原门店', 'location': '原城市旧地址'}
        observations = asyncio.run(tools.bootstrap_context())
        assert tools.context_summary()['requires_fresh_research']
        assert [row['tool'] for row in observations] == ['read_catalog', 'read_product']
        assert tools.store_research == [] and tools.place_selection is None


def test_same_turn_catalog_and_product_reread_reuse_only_fresh_bundle(identities):
    owner = identities[0]
    calls = []
    class Provider:
        def catalog(self): calls.append('catalog'); return deepcopy(CATALOG)
        def retrieve(self, name, query): calls.append(name); return bundle(name)
    with acting(owner):
        tools = AgentTools(create_conversation(), {'id': 'cold', 'text': '给甲牌手环10写朋友圈'}, provider=Provider())
        async def scenario():
            await tools.bootstrap_context()
            await tools.execute('read_catalog', {})
            result = await tools.execute('read_product', {'product': 'ALPHA Band 10', 'fact_ids': ['f1']})
            assert result['facts'][0]['id'] == 'f1'
        asyncio.run(scenario())
    assert calls == ['catalog', 'ALPHA Band 10']


def test_conflicting_or_mismatched_bundle_never_becomes_ready(identities):
    owner = identities[0]
    class Provider:
        def catalog(self): return deepcopy(CATALOG)
        def retrieve(self, name, query): return bundle('Different actual product')
    with acting(owner):
        tools = AgentTools(create_conversation(), {'id': 'cold', 'text': '给甲牌手环10写朋友圈'}, provider=Provider())
        results = asyncio.run(tools.bootstrap_context())
        assert tools.product_evidence == {}
        assert results[-1]['result']['facts'] == []
