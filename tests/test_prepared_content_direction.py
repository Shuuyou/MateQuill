"""Prepared directions reuse scoped planning, never replace fresh fact review."""
import asyncio
from copy import deepcopy

import pytest
from fastapi import HTTPException

from app.content_direction import prepared_content_direction, resolve_prepared_direction
from app.gtm_agent.marketing_opportunity import save_opportunity
from app.gtm_agent.repository import get_conversation, conversation_row, CONVERSATION
from app.models import TaskInput
from app.tenancy import service_update_body
from test_gtm_agent_core import identities, acting, start, Provider
from test_marketing_opportunity import plan


pytestmark = pytest.mark.real_tenancy


def prepared(owner):
    conversation, turn = start(owner, '准备本店内容')
    bundle = Provider().retrieve('HUAWEI MateBook Pro', '')
    proposal = {**plan(), 'evidence_ids': []}
    with acting(owner):
        saved = save_opportunity(proposal, conversation=conversation, turn=turn,
            place_context={'sources': []}, product_evidence={bundle.product: bundle})
    task = TaskInput(product=saved['product'], audience=saved['audience_hypothesis'],
        scene=saved['customer_task'], platform='moments')
    return conversation, saved, task, bundle


def test_current_scoped_plan_reuses_exact_direction_and_resets_context(identities):
    owner = identities[0]
    conversation, saved, task, bundle = prepared(owner)
    with acting(owner):
        assert resolve_prepared_direction(task, bundle) is None
        with prepared_content_direction(saved['id'], conversation_id=conversation['id'], task=task):
            resolved = resolve_prepared_direction(task, bundle)
            assert resolved['status'] == 'prepared_direction_reused'
            assert resolved['selected_concept']['angle'] == saved['content_angle']
            assert resolved['selected_concept']['visual_idea'] == saved['visual_direction']
            assert resolved['selected_concept']['fact_ids'] == saved['fact_ids']
            assert resolved['candidates'] == []
            assert 'opening' not in resolved['selected_concept']
            assert 'mechanism' not in resolved['selected_concept']
            resolved['selected_concept']['angle'] = 'MUTATED'
            assert resolve_prepared_direction(task, bundle)['selected_concept']['angle'] != 'MUTATED'
        assert resolve_prepared_direction(task, bundle) is None


def test_cross_store_plan_cannot_bind(identities):
    conversation, saved, task, bundle = prepared(identities[0])
    with acting(identities[1]), pytest.raises(HTTPException) as caught:
        with prepared_content_direction(saved['id'], conversation_id=conversation['id'], task=task):
            pytest.fail('Another store cannot enter this binding')
    assert caught.value.status_code == 404


@pytest.mark.parametrize('changed', ['product', 'audience', 'scene', 'facts', 'current'])
def test_changed_scope_or_fresh_facts_falls_back_to_normal_exploration(identities, changed):
    owner = identities[0]
    conversation, saved, task, bundle = prepared(owner)
    if changed in {'product', 'audience', 'scene'}:
        task = task.model_copy(update={changed: 'A different current task'})
    if changed == 'facts':
        bundle = bundle.model_copy(update={'facts': []})
    with acting(owner):
        if changed == 'current':
            row = conversation_row(conversation['id'])
            service_update_body(CONVERSATION, conversation['id'], {**row['body'], 'current_opportunity': None},
                                expected_version=row['version'])
        with prepared_content_direction(saved['id'], conversation_id=conversation['id'], task=task):
            assert resolve_prepared_direction(task, bundle) is None


def test_prepared_initial_direction_avoids_paid_exploration_but_true_reselect_explores(identities, monkeypatch):
    from app.content_models import build_content_model
    from app import creative_exploration, marketing_context
    owner = identities[0]
    conversation, saved, task, bundle = prepared(owner)
    model = build_content_model(calls=12)
    calls = []
    async def context(*args, **kwargs):
        return {'publisher': {}}
    async def explore(*args, **kwargs):
        calls.append('exploration')
        return {'status': 'selected', 'selected_concept': {'angle': '新的真实编辑切角'}, 'candidates': []}
    async def json_call(system, payload, temperature=.2):
        calls.append('writer')
        assert payload['editorial_concept']['angle']
        return {'title': '合成路由测试', 'intro': '', 'blocks': [], 'images': [], 'cta': '', 'tags': []}
    monkeypatch.setattr(marketing_context, 'prepare_marketing_context', context)
    monkeypatch.setattr(creative_exploration, 'explore_creative_directions', explore)
    monkeypatch.setattr(model, '_json', json_call)
    with acting(owner), prepared_content_direction(saved['id'], conversation_id=conversation['id'], task=task):
        model.prepared_creative_direction = resolve_prepared_direction(task, bundle)
        asyncio.run(model.generate(task, bundle, {}))
        assert calls == ['writer']
        assert model.creative_execution['exploration']['prepared_opportunity_id'] == saved['id']
        assert 'opening' not in model.creative_execution['exploration']['selected_concept']
        asyncio.run(model.generate(task, bundle, {'repair': True}))
        assert calls == ['writer', 'writer']
        asyncio.run(model.generate(task, bundle, {'reselect': True}))
        assert calls == ['writer', 'writer', 'exploration', 'writer']
