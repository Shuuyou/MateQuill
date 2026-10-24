"""Planner wire compatibility must preserve strict tools and real citations."""
import asyncio

import pytest
from pydantic import ValidationError

from app.gtm_agent.models import Decision
from app.gtm_agent.runtime import run_turn
from app.gtm_agent.tools import AgentTools, Arguments, planning_argument_contract
from test_gtm_agent_core import identities, acting, start, Provider, scripted
from test_marketing_opportunity import plan, research


def test_nested_delivery_scope_projects_only_known_typed_field():
    raw = {'action': 'tool', 'tool': 'prepare_opportunity',
        'arguments': {'delivery_scope': ['content', 'images'], 'opportunity': plan()}}
    decision = Decision.model_validate(raw)
    assert decision.delivery_scope == ['content', 'images']
    assert 'delivery_scope' not in decision.arguments
    assert Arguments.model_validate(decision.arguments).opportunity.title == plan()['title']
    assert 'delivery_scope' in raw['arguments']  # Never mutate a saved model response.


@pytest.mark.parametrize('outer,inner', [(['content'], ['images']),
    (None, ['publish']), (None, 'images'), (None, ['content', 'images', 'content'])])
def test_conflicting_or_invalid_delivery_scope_is_rejected(outer, inner):
    with pytest.raises(ValidationError):
        Decision.model_validate({'action': 'tool', 'tool': 'prepare_opportunity',
            'delivery_scope': outer, 'arguments': {'delivery_scope': inner}})


def test_identical_scope_in_both_locations_is_compatible_but_unknown_fields_survive_validation():
    decision = Decision.model_validate({'action': 'tool', 'tool': 'read_product',
        'delivery_scope': ['images', 'content'],
        'arguments': {'delivery_scope': ['content', 'images'], 'scope': 'tenant'}})
    with pytest.raises(ValidationError) as exc:
        Arguments.model_validate(decision.arguments)
    assert exc.value.errors()[0]['loc'] == ('scope',)
    with pytest.raises(ValidationError):
        Arguments.model_validate({'delivery_scope': ['content']})


def test_dynamic_contract_enumerates_actual_sources_without_inserting_citations():
    context = {'sources': [research()], 'creative_brief': {'evidence_ids': ['place-campus', 'unknown']}}
    schema, contract = planning_argument_contract(context)
    citation = schema['$defs']['OpportunityPlan']['properties']['evidence_ids']
    assert citation['items']['enum'] == ['place-campus']
    assert citation['minItems'] == 1
    assert 'evidence_ids' in schema['$defs']['OpportunityPlan']['required']
    assert contract['selected_grounding_evidence_ids'] == ['place-campus']
    assert context['creative_brief']['evidence_ids'] == ['place-campus', 'unknown']
    # A later cold search cannot inherit another call's IDs or minItems.
    empty, contract = planning_argument_contract(None)
    citation = empty['$defs']['OpportunityPlan']['properties']['evidence_ids']
    assert citation['maxItems'] == 0 and 'enum' not in citation['items']
    assert contract['evidence_required'] is False
    assert 'minItems' not in citation


@pytest.mark.real_tenancy
def test_planner_receives_live_grounding_contract_and_misplaced_scope_executes_once(identities):
    conversation, turn = start(identities[0], '先为海棠广场店准备一个体验机会')
    planner = scripted(
        {'action':'tool', 'tool':'research_store', 'arguments':{'query':'海棠广场 地址'}},
        {'action':'tool', 'tool':'read_product', 'arguments':{'product':'HUAWEI MateBook Pro'}},
        {'action':'tool', 'tool':'ground_store', 'arguments':{'grounding':{
            'location_status':'resolved', 'location_summary':'周边有学习空间',
            'audience':'附近同学', 'scene':'整理课程资料', 'visual_direction':'桌面实拍',
            'evidence_ids':['place-campus']}}},
        {'action':'tool', 'tool':'prepare_opportunity', 'arguments':{
            'delivery_scope':['content'], 'opportunity':plan()}},
        {'action':'reply'})
    with acting(identities[0]):
        outcome = asyncio.run(run_turn(turn['id'], planner=planner,
            tools_factory=lambda conversation, turn: AgentTools(conversation, turn,
                provider=Provider(), search=lambda query:[research()])))
    assert outcome['status'] == 'completed'
    assert len(planner.calls) == 5
    contract = planner.calls[3]['opportunity_contract']
    assert contract['allowed_evidence_ids'] == ['place-campus']
    assert contract['selected_grounding_evidence_ids'] == ['place-campus']
    assert planner.calls[4]['current_delivery_scope'] == ['content']
    assert not any('error' in row for row in planner.calls[4]['observations'])
