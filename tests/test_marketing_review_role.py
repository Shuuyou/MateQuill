"""Explicit marketing-review routing; synthetic transport is not a quality eval."""
import asyncio
from copy import deepcopy
import json

import httpx
import pytest

from app.config import Settings
from app.content_models import build_content_model
from app.llm import ModelCallError
from app.marketing_review import review_marketing
from app.product_claim_review import reconcile_marketing_product
from test_fact_review_role import configuration, transport, reply
from test_reader_value import marketing, judgement, DRAFT, TASK


def configured(**changes):
    return configuration(fact_review_use_planner=False, marketing_review_use_planner=True, **changes)


def reviewed_reply(request, *, invalid_reference=False, malformed=False):
    payload = json.loads(json.loads(request.content)['messages'][1]['content'])
    value = marketing()
    value['reader_value'] = judgement(payload['reader_value_contract'])
    value['dimensions']['product'] = {'status': 'revise', 'rationale': '具体产品命题',
        'findings': [{'quote': '', 'region': 'body:0', 'related_product_claim_id': 'UNKNOWN' if invalid_reference else 'C1',
                      'reason': '复用已核验命题', 'action': '按对应命题核对'}]}
    value['revision_brief'] = '只处理具体命题'
    if malformed:
        value['unexpected_field'] = True
    return value, payload


def test_marketing_role_default_disabled(monkeypatch):
    monkeypatch.delenv('MATEQUILL_MARKETING_REVIEW_USE_PLANNER', raising=False)
    assert Settings().marketing_review_use_planner is False
    model = build_content_model(configuration=configuration())
    assert getattr(model, 'marketing_reviewer', None) is None
    monkeypatch.setenv('MATEQUILL_MARKETING_REVIEW_USE_PLANNER', '1')
    assert Settings().marketing_review_use_planner is True


def test_review_routing_leaves_writer_and_proposal_preflight_on_content_service(monkeypatch):
    sent = []
    def handle(request):
        sent.append((request.url.host, request.headers['authorization']))
        return reply({'ok': True})
    transport(monkeypatch, handle)
    model = build_content_model(calls=3, configuration=configured())
    async def run():
        await model._json('writer', {'creative_brief': {}, 'output_contract': {}})
        await model._json('preflight', {'public_surfaces': {}, 'schema': {},
            'context': {'review_scope': '尚未发生的选题提案；不认证实际图片'}})
        await model._json('actual review', {'public_surfaces': {}, 'schema': {}, 'context': {}})
        with pytest.raises(ModelCallError, match='call_budget_exceeded'):
            await model._json('no shared budget left', {})
    asyncio.run(run())
    assert sent == [('writer.example', 'Bearer CANARY_WRITER'), ('writer.example', 'Bearer CANARY_WRITER'),
                    ('api.deepseek.com', 'Bearer CANARY_FACT')]
    assert [row['stage'] for row in model.metrics] == ['draft', 'concept_preflight', 'marketing_review']
    assert model.metrics[-1]['model'] == 'deepseek-flash'
    assert model._calls == 3 and model.marketing_reviewer._calls == 1


def test_real_marketing_review_preserves_reader_schema_and_typed_claim_reconciliation(monkeypatch):
    sent = []
    typed = {'claims': [{'claim_id': 'C1', 'surface': 'body:0', 'verdict': 'supported'}]}
    def handle(request):
        value, payload = reviewed_reply(request)
        sent.append(request.url.host)
        assert payload['context']['validated_product_claim_review'] == typed
        assert 'reader_value' in payload['schema']['required']
        return reply(value)
    transport(monkeypatch, handle)
    model = build_content_model(configuration=configured())
    result = asyncio.run(review_marketing(model, task=TASK, draft=DRAFT, evidence={},
        context={'validated_product_claim_review': typed}))
    assert result['reader_value']['status'] == 'pass'
    assert result['dimensions']['product']['findings'][0]['related_product_claim_id'] == 'C1'
    reconciled = reconcile_marketing_product(result, typed)
    assert reconciled['dimensions']['product']['status'] == 'pass'
    assert sent == ['api.deepseek.com'] and model._calls == 1


def test_unknown_claim_reference_still_fails_without_writer_fallback(monkeypatch):
    sent = []
    def handle(request):
        sent.append(request.url.host)
        return reply(reviewed_reply(request, invalid_reference=True)[0])
    transport(monkeypatch, handle)
    model = build_content_model(configuration=configured())
    with pytest.raises(ValueError, match='unknown_or_cross_surface_product_claim_reference'):
        asyncio.run(review_marketing(model, task=TASK, draft=DRAFT, evidence={},
            context={'validated_product_claim_review': {'claims': [{'claim_id': 'C1', 'surface': 'body:0'}]}}))
    assert sent == ['api.deepseek.com']


def test_strict_schema_correction_remains_within_same_parent_budget(monkeypatch):
    sent = []
    def handle(request):
        sent.append(request.url.host)
        return reply(reviewed_reply(request, malformed=len(sent) == 1)[0])
    transport(monkeypatch, handle)
    model = build_content_model(calls=2, configuration=configured())
    result = asyncio.run(review_marketing(model, task=TASK, draft=DRAFT, evidence={},
        context={'validated_product_claim_review': {'claims': [{'claim_id': 'C1', 'surface': 'body:0'}]}}))
    assert result['reader_value']['status'] == 'pass'
    assert sent == ['api.deepseek.com', 'api.deepseek.com']
    assert model._calls == model.marketing_reviewer._calls == 2


@pytest.mark.parametrize('status', [503, 524])
def test_gateway_or_service_error_does_not_retry_writer_or_change_judgement(monkeypatch, status):
    sent = []
    def handle(request):
        sent.append(request.url.host)
        return httpx.Response(status, json={'error': 'synthetic unavailable'})
    transport(monkeypatch, handle)
    model = build_content_model(configuration=configured())
    with pytest.raises(ModelCallError) as caught:
        asyncio.run(review_marketing(model, task=TASK, draft=DRAFT, evidence={}))
    assert caught.value.code == 'model_http_error' and caught.value.http_status == status
    assert sent == ['api.deepseek.com'] and model._calls == 1
    assert model.metrics[0]['outcome'] == f'model_http_error HTTP {status}'


@pytest.mark.parametrize('field', ['llm_base_url', 'llm_api_key', 'llm_model'])
def test_partial_marketing_role_cannot_borrow_content_credentials(field):
    with pytest.raises(ModelCallError, match='incomplete_marketing_review_configuration'):
        build_content_model(configuration=configured(**{field: ''}))
