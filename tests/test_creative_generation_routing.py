"""The actual writer routes validated ideation and server-owned style snapshots."""
import asyncio
from copy import deepcopy
import json

import pytest

from app.llm import OpenAICompatibleLLM
from app.models import Asset, EvidenceBundle, Fact, Source, TaskInput
from test_creative_exploration import response


@pytest.fixture
def inputs():
    task = TaskInput(product='HUAWEI FreeBuds Neo', platform='xiaohongshu',
        audience='通勤人群', scene='下班后走出写字楼，给自己留一点时间',
        requirements=json.dumps({'campaign': {'publisher_identity': 'brand'}}, ensure_ascii=False))
    bundle = EvidenceBundle(product=task.product, facts=[
        Fact(id='f1', product=task.product, label='配色', value='星河银',
             quote='星河银', source_id='s1')], sources=[
        Source(id='s1', title='官方配色', excerpt='星河银')], assets=[
        Asset(id='a1', product=task.product, source_id='s1', url='/image', title='产品配色图')])
    return task, bundle


def capture_calls(monkeypatch, model, *, exploration=None, error=None):
    calls = []
    async def generate_json(system, payload, temperature=0.2):
        calls.append((system, deepcopy(payload), temperature))
        model._calls += 1
        if 'approved_facts' in payload:
            if error:
                raise error
            return deepcopy(exploration if exploration is not None else response())
        return {'title': '下班了，耳朵也想换个频道🎧', 'intro': '',
            'blocks': [{'text': '星河银，给随身物件添一点颜色。', 'fact_ids': ['f1'], 'disclosures': []}],
            'cta': '今天想听什么？', 'images': [], 'tags': [], 'creative_plan': {},
            'creative_execution': {'forged': 'writer cannot own the execution snapshot'}}
    monkeypatch.setattr(model, '_json', generate_json)
    return calls


def test_full_writer_receives_validated_concept_voice_and_case_context(monkeypatch, inputs):
    task, bundle = inputs
    model = OpenAICompatibleLLM(api_key='test-unused', max_calls=6)
    calls = capture_calls(monkeypatch, model)
    result = asyncio.run(model.generate(task, bundle, {}))
    assert len(calls) == 2
    exploration, writer = calls[0][1], calls[1][1]
    assert writer['editorial_concept']['id'] == 'emotion'
    assert writer['editorial_concept'] == model.creative_execution['exploration']['selected_concept']
    assert exploration['account_voice'] == writer['creative_brief']['account_voice']
    assert exploration['cultural_context'] == writer['creative_brief']['cultural_context']
    assert writer['creative_brief']['account_voice']['publisher_identity'] == 'brand'
    assert writer['creative_brief']['cultural_context']['research_examples']
    assert writer['creative_brief']['cultural_context']['live_retrieval'] is False
    assert model.creative_execution['platform'] == task.platform
    assert model.creative_execution['exploration']['status'] == 'selected'
    assert model.creative_execution != result['creative_execution']
    assert 'forged' not in model.creative_execution
    # Mutation of an observed writer payload cannot rewrite frozen service state.
    writer['editorial_concept']['opening'] = 'tampered payload'
    assert model.creative_execution['exploration']['selected_concept']['opening'] != 'tampered payload'


def test_repair_reuses_frozen_concept_and_voice_without_another_exploration(monkeypatch, inputs):
    task, bundle = inputs
    model = OpenAICompatibleLLM(api_key='test-unused', max_calls=6)
    calls = capture_calls(monkeypatch, model)
    asyncio.run(model.generate(task, bundle, {}))
    frozen = deepcopy(model.creative_execution)
    frozen['account_voice']['learned_preferences'] = [{'text': '保留短句与一点轻幽默。'}]
    original = deepcopy(frozen)
    calls.clear()
    asyncio.run(model.generate(task, bundle, {'repair': True, 'creative_execution': frozen}))
    assert len(calls) == 1
    assert 'approved_facts' not in calls[0][1]
    assert calls[0][1]['editorial_concept'] == original['exploration']['selected_concept']
    assert calls[0][1]['creative_brief']['account_voice'] == original['account_voice']
    assert calls[0][1]['creative_brief']['cultural_context'] == original['cultural_context']
    assert model.creative_execution == original
    assert frozen == original
    frozen['account_voice']['learned_preferences'].clear()
    assert model.creative_execution == original


def test_small_budget_still_writes_with_explicit_skipped_ideation(monkeypatch, inputs):
    model = OpenAICompatibleLLM(api_key='test-unused', max_calls=4)
    calls = capture_calls(monkeypatch, model)
    result = asyncio.run(model.generate(*inputs, {}))
    assert result['title']
    assert len(calls) == 1
    assert calls[0][1]['editorial_concept'] is None
    assert calls[0][1]['creative_brief']['account_voice']['emoji_policy']
    assert model.creative_execution['exploration']['status'] == 'skipped'
    assert model.creative_execution['exploration']['reason_code'] == 'call_budget_reserved'


@pytest.mark.parametrize('mode', ['provider_error', 'invalid_schema'])
def test_failed_ideation_does_not_prevent_grounded_writer_or_fake_success(monkeypatch, inputs, mode):
    model = OpenAICompatibleLLM(api_key='test-unused', max_calls=6)
    calls = capture_calls(monkeypatch, model,
        error=RuntimeError('provider unavailable') if mode == 'provider_error' else None,
        exploration={'selected_id': 'invented', 'candidates': []} if mode == 'invalid_schema' else None)
    result = asyncio.run(model.generate(*inputs, {}))
    assert result['blocks'][0]['fact_ids'] == ['f1']
    assert len(calls) == 2
    assert calls[1][1]['editorial_concept'] is None
    assert model.creative_execution['exploration']['status'] == 'fallback'
    assert model.creative_execution['exploration']['selected_concept'] is None
    assert [fact['id'] for fact in calls[1][1]['evidence']['facts']] == ['f1']


@pytest.mark.parametrize('strategy,expected', [
    ({}, 'brand'),
    ({'priority_fact_ids': ['f1']}, 'brand'),
    ({'priority_fact_ids': ['f1'], 'strategy_context': {'publisher_identity': 'store'}}, 'store'),
])
def test_resolved_identity_reaches_both_voice_and_case_selection(monkeypatch, inputs, strategy, expected):
    model = OpenAICompatibleLLM(api_key='test-unused', max_calls=4)
    calls = capture_calls(monkeypatch, model)
    asyncio.run(model.generate(*inputs, strategy))
    brief = calls[0][1]['creative_brief']
    assert brief['strategy_snapshot']['context']['publisher_identity'] == expected
    assert brief['account_voice']['publisher_identity'] == expected
    for case in brief['cultural_context']['research_examples']:
        assert case['identity_transfer'] == (
            'same_identity' if case['publisher_identity'] == expected else 'adapt_mechanism_to_publisher')
