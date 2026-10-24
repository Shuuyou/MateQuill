"""Synthetic latency routing checks; no provider calls or quality claims."""
import asyncio
from copy import deepcopy
from types import SimpleNamespace

import httpx
import pytest

from app.content_models import build_content_model, content_timing_snapshot
from app.llm import OpenAICompatibleLLM
from app.models import EvidenceBundle, RunResult, TaskInput


def configuration():
    return SimpleNamespace(content_base_url='https://synthetic.example/v1',
        content_api_key='synthetic-not-secret', content_model='gpt-6.1-sol')


def test_interactive_factory_keeps_concept_exploration_and_actual_draft_reviews(monkeypatch):
    from app import creative_exploration, marketing_context
    from app.marketing_review import DIMENSIONS
    from app.product_claim_review import product_claim_surfaces

    model = build_content_model(calls=12, configuration=configuration())
    # This test exercises routing, not the independent reader contract schema.
    model.supports_reader_value_contract = False
    task = TaskInput(product='Synthetic phone', audience='附近散步的朋友', scene='晚间散步', platform='moments')
    bundle = EvidenceBundle(product=task.product)
    draft = {'title': '看看街角的光', 'intro': '', 'blocks': [{'text': '走到街角，抬头看看。'}],
             'cta': '今天你看见了什么？', 'tags': [], 'images': [], 'creative_plan': {}}
    calls = []
    concepts = []

    async def context(*args, **kwargs):
        return {'publisher': {}}

    async def explore(*args, **kwargs):
        concepts.append(True)
        return {'selected_concept': {'opening': '看看街角的光', 'angle': '抬头', 'payoff': '观察街角'},
                'candidates': [], 'status': 'selected'}

    async def json_call(system, payload, temperature=.2):
        if 'assessment_scope' in payload:
            calls.append('fact_review')
            return {'verdict': 'no_product_claims', 'audit': [], 'product_claim_review': {
                'coverage': {key: 'no_product_claim' for key in product_claim_surfaces(draft)}, 'claims': []}}
        if 'public_surfaces' in payload:
            assert not str(payload.get('context', {}).get('review_scope', '')).startswith('尚未发生')
            calls.append('marketing_review')
            return {'schema_version': '1', 'phase': 'draft', 'decision': 'accept',
                'dimensions': {key: {'status': 'pass', 'rationale': '合成路由测试', 'findings': []} for key in DIMENSIONS},
                'creative_effectiveness': {'status': 'pass', 'hook': '合成测试', 'local_value': '合成测试',
                    'brand_connection': '合成测试', 'natural_action': '合成测试', 'distinctiveness': '合成测试',
                    'preserve': [], 'improvements': []}, 'revision_brief': ''}
        calls.append('draft')
        return deepcopy(draft)

    monkeypatch.setattr(marketing_context, 'prepare_marketing_context', context)
    monkeypatch.setattr(creative_exploration, 'explore_creative_directions', explore)
    monkeypatch.setattr(model, '_json', json_call)
    actual = asyncio.run(model.generate(task, bundle, {}))
    result = RunResult.model_validate(actual)
    audit = asyncio.run(model.verify(result, bundle))
    assert concepts == [True]
    assert calls == ['draft', 'fact_review', 'marketing_review']
    assert any(item.category == 'semantic' and item.severity == 'info' for item in audit)
    assert len([item for item in audit if item.category.startswith('marketing_')]) == 6


def test_batch_can_explicitly_keep_preflight_without_changing_call_budget():
    model = build_content_model(calls=9, tokens=3300, timeout=81,
        configuration=configuration(), concept_preflight=True)
    assert model.concept_preflight_enabled is True
    assert (model.max_calls, model.max_tokens, model.timeout) == (9, 3300, 81)


def test_cancelled_model_request_records_actual_stage_without_false_success(monkeypatch):
    client = httpx.AsyncClient
    async def handler(request):
        await asyncio.sleep(10)
        pytest.fail('The local deadline must cancel this synthetic response')
    monkeypatch.setattr('app.llm.httpx.AsyncClient', lambda **kwargs:
        client(transport=httpx.MockTransport(handler), **kwargs))
    model = OpenAICompatibleLLM(base_url='https://synthetic.example/v1', api_key='CANARY', max_calls=3)
    async def run():
        async with asyncio.timeout(.02):
            await model._json('CANARY_SYSTEM', {'approved_facts': [], 'output_schema': {}})
    with pytest.raises(TimeoutError):
        asyncio.run(run())
    assert model._calls == 1
    assert model.metrics[0]['stage'] == 'concept'
    assert model.metrics[0]['outcome'] == 'cancelled'
    assert 'CANARY' not in str(model.metrics)


def test_timing_projection_restricts_fields_and_untrusted_stage_strings():
    model = SimpleNamespace(metrics=[{'stage': 'CANARY', 'duration_ms': 25, 'body': 'CANARY'},
                                    {'stage': 'fact_review', 'duration_ms': 75}])
    snapshot = content_timing_snapshot(model)
    assert snapshot == {'completed_calls': 2, 'model_duration_ms': 100,
                        'calls': [{'stage': 'model', 'duration_ms': 25}, {'stage': 'fact_review', 'duration_ms': 75}]}
    assert 'CANARY' not in str(snapshot)
