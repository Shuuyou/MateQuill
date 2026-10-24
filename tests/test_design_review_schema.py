import asyncio
from copy import deepcopy
import json

import httpx
import pytest

from app.design.review_schema import (
    EDIT_FIELDS, UNSUPPORTED_COMPOSITION, decode_nullable_edits,
    explicitly_unsupported_schema, supports_strict_visual_schema, visual_review_schema,
)
from app.design_review import review_with_model, validate_marketing_visual_review
from app.marketing_review import MARKETING_REVIEW_SCHEMA
from test_design_marketing_visual import review_payload
from test_design_review import doc, render


def nodes(value):
    if isinstance(value, dict):
        yield value
        for item in value.values():
            yield from nodes(item)
    elif isinstance(value, list):
        for item in value:
            yield from nodes(item)


def payload():
    return {'review_completed': True, 'score': 90,
        'checks': [{'category': key, 'severity': 'info', 'message': 'Actual-pixel fixture observation'}
                   for key in ('composition', 'typography', 'imagery', 'platform', 'specificity')],
        'edits': [], 'marketing_review': review_payload()}


def test_combined_schema_requires_every_field_and_keeps_server_conditions_unchanged():
    original = deepcopy(MARKETING_REVIEW_SCHEMA)
    schema = visual_review_schema(marketing_required=True)
    assert set(schema['required']) == {'review_completed', 'score', 'checks', 'edits', 'marketing_review'}
    for node in nodes(schema):
        assert not UNSUPPORTED_COMPOSITION.intersection(node)
        if node.get('type') == 'object':
            assert node['additionalProperties'] is False
            assert set(node['required']) == set(node['properties'])
    assert schema['$defs']['DimensionReview']['required'] == ['status', 'rationale', 'findings']
    assert schema['properties']['marketing_review']['properties']['phase']['enum'] == ['final']
    assert 'decision' not in schema['properties']['marketing_review']['properties']
    assert MARKETING_REVIEW_SCHEMA == original
    assert 'allOf' in MARKETING_REVIEW_SCHEMA['$defs']['DimensionReview']


def test_nullable_edit_slots_are_required_but_decoder_only_removes_none_changes():
    schema = visual_review_schema(marketing_required=True)
    changes = schema['properties']['edits']['items']['properties']['changes']
    assert set(changes['required']) == set(EDIT_FIELDS)
    assert all({'type': 'null'} in value['anyOf'] for value in changes['properties'].values())
    raw = payload()
    raw['edits'] = [{'id': 'headline', 'changes': {key: None for key in EDIT_FIELDS}}]
    raw['edits'][0]['changes'].update(x=0, letter_spacing=0)
    raw['marketing_review']['dimensions']['timeliness']['status'] = 'not_applicable'
    del raw['marketing_review']['dimensions']['timeliness']['findings']
    decoded = decode_nullable_edits(raw)
    assert decoded['edits'][0]['changes'] == {'x': 0, 'letter_spacing': 0}
    assert 'findings' not in decoded['marketing_review']['dimensions']['timeliness']
    assert len(raw['edits'][0]['changes']) == len(EDIT_FIELDS)


@pytest.mark.parametrize('model,expected', [
    ('gpt-4.1', True), ('gpt-4.1-2025-04-14', True), ('gpt-4o', True),
    ('gpt-6', True), ('gpt-6-astra', True), ('gpt-6.1-sol', True),
    ('vl', False), ('deepseek-flash', False),
])
def test_known_model_families(model, expected):
    assert supports_strict_visual_schema(model) is expected


def install_transport(monkeypatch, handler):
    actual = httpx.AsyncClient
    monkeypatch.setattr(httpx, 'AsyncClient', lambda **kwargs: actual(transport=httpx.MockTransport(handler), **kwargs))


def run_model():
    document = doc()
    return asyncio.run(review_with_model(render(document), document, 'https://vision.example/v1', 'TEST-ONLY',
        'gpt-4.1', design_context={'marketing_context': {'published_copy': {'body': 'real title'}}}))


def test_actual_request_sends_strict_combined_schema_and_decodes_null_edit_slots(monkeypatch):
    captured = []
    def handler(request):
        body = json.loads(request.content)
        captured.append(body)
        assert body['response_format']['type'] == 'json_schema'
        strict = body['response_format']['json_schema']
        assert strict['strict'] is True
        assert 'findings' in strict['schema']['$defs']['DimensionReview']['required']
        result = payload()
        result['edits'] = [{'id': 'headline', 'changes': {key: None for key in EDIT_FIELDS}}]
        result['edits'][0]['changes']['x'] = 10
        return httpx.Response(200, json={'choices': [{'message': {'content': json.dumps(result)}}]})
    install_transport(monkeypatch, handler)
    result = run_model()
    assert len(captured) == 1 and result['response_format_used'] == 'json_schema'
    assert result['edits'] == [{'id': 'headline', 'changes': {'x': 10}}]
    assert result['marketing_review']['action'] == 'deliver'


@pytest.mark.parametrize('status', ['not_applicable', 'unverified'])
def test_missing_or_unresolved_empty_findings_still_server_incomplete(monkeypatch, status):
    calls = []
    def handler(request):
        calls.append(True)
        result = payload()
        result['marketing_review']['dimensions']['timeliness']['status'] = status
        if status == 'not_applicable':
            del result['marketing_review']['dimensions']['timeliness']['findings']
        return httpx.Response(200, json={'choices': [{'message': {'content': json.dumps(result)}}]})
    install_transport(monkeypatch, handler)
    result = run_model()
    assert calls == [True]
    assert result['marketing_review']['action'] == 'incomplete'
    assert not result['marketing_review']['review_completed']
    assert result['marketing_review']['diagnostic']['error_class'] == 'validation'


def test_only_explicit_400_schema_capability_rejection_has_one_fallback(monkeypatch):
    formats = []
    def handler(request):
        formats.append(json.loads(request.content)['response_format']['type'])
        if len(formats) == 1:
            return httpx.Response(400, json={'error': {'message': 'response_format json_schema is not supported by this endpoint'}})
        return httpx.Response(200, json={'choices': [{'message': {'content': json.dumps(payload())}}]})
    install_transport(monkeypatch, handler)
    result = run_model()
    assert formats == ['json_schema', 'json_object']
    assert result['response_format_used'] == 'json_object'
    assert result['marketing_review']['action'] == 'deliver'


@pytest.mark.parametrize('status,message', [
    (400, 'Invalid schema for response_format: unsupported allOf'),
    (400, 'Invalid image input'), (401, 'json_schema unsupported'), (500, 'json_schema unsupported'),
])
def test_other_http_failures_never_reissue_review(monkeypatch, status, message):
    calls = []
    def handler(request):
        calls.append(True)
        return httpx.Response(status, json={'error': {'message': message}})
    install_transport(monkeypatch, handler)
    assert run_model()['review_completed'] is False
    assert calls == [True]


def test_timeout_never_retries_unknown_request(monkeypatch):
    calls = []
    def handler(request):
        calls.append(True)
        raise httpx.ReadTimeout('unknown completion state', request=request)
    install_transport(monkeypatch, handler)
    assert run_model()['review_completed'] is False
    assert calls == [True]
