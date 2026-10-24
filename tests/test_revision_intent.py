"""Wording edits bind existing context; context deltas still need research."""
import asyncio
from copy import deepcopy
from uuid import uuid4

import pytest
from fastapi import HTTPException
from pydantic import ValidationError

from app.gtm_agent import jobs
from app.gtm_agent.models import TurnInput
from app.gtm_agent.repository import create_conversation, get_conversation, link_artifact, TURN
from app.gtm_agent.runtime import run_turn
from app.gtm_agent.tools import AgentTools, Arguments
from app.tenancy import authorize_resource, register_resource
from test_gtm_agent_core import identities, acting, scripted

pytestmark = pytest.mark.real_tenancy


def revision_setup():
    body = {'run_id':'wording-parent', 'status':'ready', 'title':'原标题',
        'blocks':[{'text':'旧的开头'}, {'text':'保留的第二段'}],
        'facts':[{'id':'fact-original'}], 'sources':[{'id':'source-original'}],
        '_poster_task':{'product':'HUAWEI MateBook Pro', 'audience':'附近学生',
            'scene':'课后整理资料', 'requirements':'原始来源和限定条件'}}
    register_resource('run', 'wording-parent', body=deepcopy(body))
    conversation = create_conversation()
    link_artifact(conversation['id'], 'run', 'wording-parent')
    turn = jobs.enqueue_turn(conversation['id'], TurnInput(
        text='只改开头更自然，不要编写天天看顾客散场；保留我在海棠广场店上班的身份，其余原样。',
        client_turn_id=uuid4().hex))
    return get_conversation(conversation['id']), authorize_resource(TURN, turn['id'])['body'], body


@pytest.mark.parametrize('failed_research_already_observed', [False, True])
def test_wording_edit_repeated_store_name_reaches_reviser_without_research(identities, failed_research_already_observed):
    with acting(identities[0]):
        conversation, turn, original = revision_setup()
        calls = []
        if failed_research_already_observed:
            turn = {**turn, 'observations':[{'tool':'research_store', 'result':{
                'items':[], 'state':'no_results', 'network_state':'unavailable'}}]}
        async def revise(identifier, instruction, **kwargs):
            calls.append((identifier, instruction, kwargs))
            child = {**deepcopy(original), 'run_id':'wording-child',
                'blocks':[{'text':'如果夜里走出自习室，抬头也许会注意到灯光。'}, original['blocks'][1]]}
            register_resource('run', 'wording-child', body=child)
            return child
        def no_search(*_):
            pytest.fail('A source-bound expression edit must not request network research')
        planner = scripted({'action':'tool', 'tool':'read_artifact'},
            {'action':'tool', 'tool':'revise_content', 'arguments':{
                'instruction':'只修正开头的表达，不要虚构观察，其余保持原样。',
                'revision_intent':{'kind':'wording_only', 'source_run_id':'wording-parent'}}})
        result = asyncio.run(run_turn(turn['id'], planner=planner,
            tools_factory=lambda c, _:AgentTools(c, turn, search=no_search, reviser=revise)))
        assert result['status'] == 'completed'
        assert result['artifact']['id'] == 'wording-child'
        assert len(planner.calls) == 2 and len(calls) == 1
        assert calls[0][2] == {}  # No fresh task context or location override.
        assert authorize_resource('run', 'wording-parent')['body'] == original
        child = authorize_resource('run', 'wording-child')['body']
        for key in ('_poster_task', 'facts', 'sources'):
            assert child[key] == original[key]


@pytest.mark.parametrize('delta', [{'location':'另一城市的新门店'}, {'audience':'附近教师'}])
def test_context_delta_requires_new_research_and_grounding(identities, delta):
    with acting(identities[0]):
        conversation, turn, original = revision_setup()
        calls = []
        async def revise(identifier, instruction, **kwargs):
            calls.append(kwargs)
            child = {**deepcopy(original), 'run_id':'context-child'}
            register_resource('run', 'context-child', body=child)
            return child
        tools = AgentTools(conversation, turn, search=lambda query:[{
            'source_id':'new-place', 'title':'新地点的真实测试来源',
            'url':'https://public.example/new-place', 'excerpt':'周边有教师生活空间'}], reviser=revise)
        arguments = {'instruction':'使用新的地方或人群',
            'revision_intent':{'kind':'context_change', 'source_run_id':'wording-parent', **delta}}
        with pytest.raises(HTTPException) as exc:
            asyncio.run(tools.execute('revise_content', arguments))
        assert exc.value.status_code == 422 and not calls
        asyncio.run(tools.execute('research_store', {'query':'新地点 周边'}))
        with pytest.raises(HTTPException):
            asyncio.run(tools.execute('revise_content', arguments))
        assert not calls
        asyncio.run(tools.execute('ground_store', {'grounding':{
            'location_status':'resolved', 'location_summary':'新来源中的地点',
            'audience':'附近教师', 'scene':'备课后整理资料', 'visual_direction':'备课桌面',
            'evidence_ids':['new-place']}}))
        asyncio.run(tools.execute('revise_content', arguments))
        assert calls[0]['task_override'].audience == '附近教师'
        assert authorize_resource('run', 'wording-parent')['body'] == original


def test_wording_intent_is_source_bound_and_cannot_carry_context_fields(identities):
    with pytest.raises(ValidationError):
        Arguments.model_validate({'revision_intent':{'kind':'wording_only',
            'source_run_id':'original', 'location':'another place'}})
    with pytest.raises(ValidationError):
        Arguments.model_validate({'revision_intent':{'kind':'context_change', 'source_run_id':'original'}})
    with acting(identities[0]):
        conversation, turn, _ = revision_setup()
        tools = AgentTools(conversation, turn, reviser=lambda *_:pytest.fail('Invalid intent cannot call model'))
        for arguments in (
            {'revision_intent':{'kind':'wording_only', 'source_run_id':'different-run'}},
            {'audience':'different people', 'revision_intent':{'kind':'wording_only', 'source_run_id':'wording-parent'}}):
            with pytest.raises(HTTPException):
                asyncio.run(tools.execute('revise_content', arguments))
        with pytest.raises(HTTPException):
            asyncio.run(tools.execute('read_catalog', {'revision_intent':{
                'kind':'wording_only', 'source_run_id':'wording-parent'}}))
