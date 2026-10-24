"""Grounded location corrections survive real revision persistence and design IO."""
import asyncio
from copy import deepcopy
from uuid import uuid4

import pytest
from fastapi import HTTPException
from pydantic import ValidationError

from test_creative_revision import Model, workspace
from test_gtm_agent_core import scripted
from app.creative_revision import RevisionTaskOverride, revise_creation
from app.gtm_agent import jobs
from app.gtm_agent.models import TurnInput
from app.gtm_agent.repository import create_conversation, link_artifact, get_turn
from app.gtm_agent.tools import AgentTools
from app.tenancy import authorize_resource, register_resource

pytestmark = pytest.mark.real_tenancy


def test_grounded_revision_updates_saved_task_and_next_design_brief(workspace):
    parent, _ = workspace
    register_resource('store', 's1', body={'name': '海棠广场店'})
    conversation = create_conversation()
    link_artifact(conversation['id'], 'run', 'parent')
    instruction = '换成面向附近教师的表达，保留照片'
    turn = jobs.enqueue_turn(conversation['id'], TurnInput(text=instruction, client_turn_id=uuid4().hex))
    writer = Model({'changes': [{'field': 'title', 'value': '给备课后的桌面留一点光'}]})
    forwarded = []

    async def revise(identifier, text, **kwargs):
        forwarded.append((text, kwargs))
        return await revise_creation(identifier, text, llm=writer, **kwargs)

    planner = scripted(
        {'action': 'tool', 'tool': 'read_artifact'},
        {'action': 'tool', 'tool': 'research_store', 'arguments': {'query': '"海棠广场" 地址 周边'}},
        {'action': 'tool', 'tool': 'ground_store', 'arguments': {'grounding': {
            'location_status': 'resolved', 'location_summary': '海棠广场位于大学路',
            'audience': '附近高校教师', 'scene': '晚间备课后整理桌面',
            'visual_direction': '真实备课桌面与课件笔记', 'evidence_ids': ['place-school']}}},
        {'action': 'tool', 'tool': 'revise_content', 'arguments': {'instruction': instruction}})
    evidence = [{'source_id': 'place-school', 'title': '海棠广场周边',
        'url': 'https://public.example/place', 'excerpt': '位于大学路，周边有高校和教师公寓。',
        'observed_at': '2026-10-22T12:00:00+08:00', 'provider': 'test_search'}]
    asyncio.run(jobs.execute_claimed(jobs.claim_next(), planner=planner,
        tools_factory=lambda c, t: AgentTools(c, t, search=lambda _: evidence, reviser=revise)))
    finished = get_turn(conversation['id'], turn['id'])
    assert finished['status'] == 'completed'
    child = authorize_resource('run', finished['artifact']['id'])['body']
    assert forwarded[0][0] == instruction
    assert isinstance(forwarded[0][1]['task_override'], RevisionTaskOverride)
    assert writer.calls[0][1]['instruction'] == instruction
    assert writer.calls[0][1]['task_context']['audience'] == '附近高校教师'
    assert child['_poster_task']['audience'] == '附近高校教师'
    assert child['_poster_task']['scene'] == '晚间备课后整理桌面'
    assert '真实备课桌面与课件笔记' in child['_poster_task']['requirements']
    for key in ('images', 'assets', 'facts', 'sources'):
        assert child[key] == parent[key]
    assert authorize_resource('run', 'parent')['body'] == parent

    # Use the same source-reading and planner entry points as image production.
    from app.design_service import source_record
    from app.design.planner import plan_design
    source, task = source_record(child['run_id'])
    initial = asyncio.run(plan_design(task.model_dump(), source, platform='moments'))
    payloads = []

    class ArtDirector:
        async def _json(self, system, payload, **kwargs):
            payloads.append(deepcopy(payload))
            return {'selected_concept_id': initial.selected_concept_id,
                    'concepts': [concept.model_dump() for concept in initial.concepts]}

    designed = asyncio.run(plan_design(task.model_dump(), source, platform='moments', llm=ArtDirector()))
    assert designed.provenance['planner'] == 'model_art_direction'
    assert designed.brief.audience == '附近高校教师'
    assert designed.brief.scene == '晚间备课后整理桌面'
    assert '真实备课桌面与课件笔记' in designed.brief.requirements
    assert payloads[0]['brief']['audience'] == designed.brief.audience
    assert payloads[0]['brief']['scene'] == designed.brief.scene
    assert payloads[0]['brief']['requirements'] == designed.brief.requirements


def test_untyped_or_identity_changing_revision_context_is_rejected(workspace):
    model = Model()
    with pytest.raises(HTTPException) as error:
        asyncio.run(revise_creation('parent', '只改标题', llm=model,
            task_override={'audience': '同学', 'scene': '下课', 'requirements': ''}))
    assert error.value.status_code == 422
    assert not model.calls
    with pytest.raises(ValidationError):
        RevisionTaskOverride(audience='同学', scene='下课', requirements='', product='另一款产品')
    from app.creative_agent_routes import RevisionInput
    with pytest.raises(ValidationError):
        RevisionInput(instruction='只改标题', task_override={'audience': '同学'})
