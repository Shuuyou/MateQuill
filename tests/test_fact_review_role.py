"""Explicit role routing and budgets using synthetic HTTP responses only."""
import asyncio
import json
from types import SimpleNamespace

import httpx
import pytest

from app.config import Settings
from app.content_models import build_content_model, content_timing_snapshot
from app.llm import ModelCallError, OpenAICompatibleLLM
from app.models import EvidenceBundle, RunResult, TaskInput


def configuration(**changes):
    return SimpleNamespace(**{'content_base_url': 'https://writer.example/v1',
        'content_api_key': 'CANARY_WRITER', 'content_model': 'gpt-6.1-sol',
        'llm_base_url': 'https://api.deepseek.com', 'llm_api_key': 'CANARY_FACT',
        'llm_model': 'deepseek-flash', 'fact_review_use_planner': True, **changes})


def transport(monkeypatch, handler):
    original = httpx.AsyncClient
    monkeypatch.setattr('app.llm.httpx.AsyncClient', lambda **kwargs:
        original(transport=httpx.MockTransport(handler), **kwargs))


def reply(value):
    return httpx.Response(200, json={'choices': [{'finish_reason': 'stop',
        'message': {'content': value if isinstance(value, str) else json.dumps(value)}}],
        'usage': {'prompt_tokens': 12, 'completion_tokens': 8, 'total_tokens': 20}})


def test_role_is_disabled_by_default_and_enabled_only_by_explicit_flag(monkeypatch):
    monkeypatch.delenv('MATEQUILL_FACT_REVIEW_USE_PLANNER', raising=False)
    assert Settings().fact_review_use_planner is False
    model = build_content_model(configuration=configuration(fact_review_use_planner=False))
    assert getattr(model, 'fact_reviewer', None) is None
    monkeypatch.setenv('MATEQUILL_FACT_REVIEW_USE_PLANNER', '1')
    assert Settings().fact_review_use_planner is True


def test_only_assessment_request_uses_explicit_role_and_shared_metrics(monkeypatch):
    sent = []
    def handle(request):
        body = json.loads(request.content)
        sent.append((request.url.host, request.headers['authorization'], body))
        return reply({'ok': True})
    transport(monkeypatch, handle)
    model = build_content_model(calls=2, tokens=3200, configuration=configuration())
    async def run():
        await model._json('writer', {'creative_brief': {}, 'output_contract': {}})
        await model._json('fact reviewer', {'assessment_scope': {'target': 'claims_actually_made'}})
        with pytest.raises(ModelCallError, match='call_budget_exceeded'):
            await model._json('no remaining budget', {})
    asyncio.run(run())
    assert [row[0] for row in sent] == ['writer.example', 'api.deepseek.com']
    assert [row[1] for row in sent] == ['Bearer CANARY_WRITER', 'Bearer CANARY_FACT']
    assert sent[0][2]['reasoning_effort'] == 'low'
    assert sent[1][2]['thinking'] == {'type': 'disabled'}
    assert sent[1][2]['max_tokens'] == 3200
    assert model._calls == 2 and model.fact_reviewer._calls == 1
    assert [row['call'] for row in model.metrics] == [1, 2]
    assert [row['model'] for row in model.metrics] == ['gpt-6.1-sol', 'deepseek-flash']
    assert model.metrics[-1]['total_tokens'] == 20
    assert content_timing_snapshot(model)['calls'][-1]['model'] == 'deepseek-flash'
    assert 'CANARY' not in str(model.metrics) + str(content_timing_snapshot(model))


def test_malformed_fact_json_retries_only_within_parent_shared_budget(monkeypatch):
    sent = []
    def handle(request):
        sent.append(request.url.host)
        return reply('not json' if len(sent) == 1 else {'ok': True})
    transport(monkeypatch, handle)
    model = build_content_model(calls=2, configuration=configuration())
    assert asyncio.run(model._json('fact reviewer', {'assessment_scope': {}})) == {'ok': True}
    assert sent == ['api.deepseek.com', 'api.deepseek.com']
    assert model._calls == model.fact_reviewer._calls == 2
    assert [row['outcome'] for row in model.metrics] == ['invalid_model_json', 'success']


def test_fact_service_failure_never_falls_back_to_writer(monkeypatch):
    sent = []
    def handle(request):
        sent.append(request.url.host)
        return httpx.Response(503, json={'error': 'synthetic failure'})
    transport(monkeypatch, handle)
    model = build_content_model(configuration=configuration())
    with pytest.raises(ModelCallError) as caught:
        asyncio.run(model._json('fact reviewer', {'assessment_scope': {}}))
    assert caught.value.code == 'model_http_error' and caught.value.http_status == 503
    assert sent == ['api.deepseek.com'] and model._calls == 1
    assert model.metrics[0]['stage'] == 'fact_review'


def test_cancelled_fact_request_has_one_shared_failed_metric(monkeypatch):
    async def handle(request):
        await asyncio.sleep(10)
        pytest.fail('Synthetic request must be cancelled')
    transport(monkeypatch, handle)
    model = build_content_model(configuration=configuration())
    async def run():
        async with asyncio.timeout(.02):
            await model._json('fact reviewer', {'assessment_scope': {}})
    with pytest.raises(TimeoutError):
        asyncio.run(run())
    assert model._calls == model.fact_reviewer._calls == 1
    assert model.metrics[0]['outcome'] == model.fact_reviewer.metrics[0]['outcome'] == 'cancelled'


@pytest.mark.parametrize('field', ['llm_base_url', 'llm_api_key', 'llm_model'])
def test_partial_fact_role_does_not_borrow_writer_credentials(field):
    with pytest.raises(ModelCallError) as caught:
        build_content_model(configuration=configuration(**{field: ''}))
    assert caught.value.code == 'incomplete_fact_review_configuration'
    assert 'CANARY' not in str(caught.value) and 'https://' not in str(caught.value)


def test_explicit_injected_reviewer_preserves_its_connection_and_no_default_fallback():
    child = OpenAICompatibleLLM('https://reviewer.example/v1', 'synthetic', 'review-model', timeout=17)
    model = build_content_model(configuration=configuration(fact_review_use_planner=False), fact_reviewer=child)
    assert model.fact_reviewer is child and child.timeout == 17
    with pytest.raises(ModelCallError, match='invalid_fact_review_configuration'):
        build_content_model(configuration=configuration(), fact_reviewer=OpenAICompatibleLLM(api_key=''))


def test_typed_fact_guard_still_rejects_unknown_evidence_from_reviewer(monkeypatch):
    from test_product_claim_review import typed
    raw, draft, evidence = typed()
    raw['claims'][0]['evidence_ids'] = ['unknown-evidence']
    sent = []
    def handle(request):
        sent.append(request.url.host)
        return reply({'verdict': 'supported', 'audit': [], 'product_claim_review': raw})
    transport(monkeypatch, handle)
    model = build_content_model(configuration=configuration())
    model.marketing_task = TaskInput(product='Synthetic phone', audience='附近的朋友', scene='散步')
    bundle = EvidenceBundle(product='Synthetic phone', facts=[{
        'id': 'camera', 'product': 'Synthetic phone', 'label': '相机', 'value': evidence['facts'][0]['value'],
        'quote': evidence['facts'][0]['quote'], 'source_id': 's1'}])
    with pytest.raises(ModelCallError, match='invalid_product_claim_review_schema'):
        asyncio.run(model.verify(RunResult.model_validate(draft), bundle))
    assert sent == ['api.deepseek.com']
    assert model._calls == 1
