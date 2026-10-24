"""Role selection is explicit; no transport calls or credentials cross services."""
import json
import asyncio
from types import SimpleNamespace

import httpx
import pytest

from app.config import Settings
from app.content_models import ContentLLM, build_content_model
from app.llm import ModelCallError, OpenAICompatibleLLM


def configuration(**overrides):
    return SimpleNamespace(llm_base_url='https://original.example/v1',
        llm_api_key='original-synthetic', llm_model='original-model',
        **overrides)


def test_unset_role_preserves_existing_provider_and_exact_caller_budgets():
    model = build_content_model(calls=7, tokens=4100, timeout=123,
                                configuration=configuration())
    assert isinstance(model, OpenAICompatibleLLM)
    assert (model.base_url, model.api_key, model.model) == (
        'https://original.example/v1', 'original-synthetic', 'original-model')
    assert (model.max_calls, model.max_tokens, model.timeout, model._calls) == (7, 4100, 123, 0)
    assert model.metrics == []


def test_complete_role_uses_only_explicit_content_connection():
    model = build_content_model(configuration=configuration(
        content_base_url='https://content.example/v1/', content_api_key='content-synthetic',
        content_model='quality-model', image_api_key='wrong-image-synthetic',
        image_base_url='https://image.example/v1', design_text_model='not-content'))
    assert (model.base_url, model.api_key, model.model) == (
        'https://content.example/v1', 'content-synthetic', 'quality-model')
    assert model._calls == 0 and model.metrics == []


@pytest.mark.parametrize('values', [
    {'content_model': 'quality-model'},
    {'content_base_url': 'https://content.example/v1'},
    {'content_api_key': 'content-synthetic'},
    {'content_base_url': 'https://content.example/v1', 'content_model': 'quality-model'},
    {'content_base_url': 'https://content.example/v1', 'content_api_key': 'content-synthetic'},
    {'content_api_key': 'content-synthetic', 'content_model': 'quality-model'},
])
def test_partial_role_fails_before_constructing_or_sending_any_provider_key(values, monkeypatch):
    import app.content_models as module
    monkeypatch.setattr(module, 'OpenAICompatibleLLM', lambda *args, **kwargs: pytest.fail('No client construction'))
    with pytest.raises(ModelCallError) as caught:
        module.build_content_model(configuration=configuration(**values))
    assert caught.value.code == 'incomplete_content_model_configuration'
    assert 'synthetic' not in str(caught.value) and 'https://' not in str(caught.value)


def test_whitespace_only_fields_preserve_original_and_do_not_activate_image_credentials():
    model = build_content_model(configuration=configuration(content_base_url=' ', content_api_key='\n',
        content_model='\t', image_api_key='wrong-image-synthetic', design_text_model='wrong-model'))
    assert model.model == 'original-model' and model.api_key == 'original-synthetic'


def test_content_credentials_never_appear_in_public_settings_or_dataclass_repr(monkeypatch):
    for key, value in {'CONTENT_BASE_URL': 'https://content-private.example/v1',
                       'CONTENT_API_KEY': 'content-synthetic', 'CONTENT_MODEL': 'quality-model'}.items():
        monkeypatch.setenv(key, value)
    settings = Settings()
    assert settings.content_model == 'quality-model'
    public = json.dumps(settings.public())
    assert 'content-synthetic' not in public and 'content-private.example' not in public
    assert 'content-synthetic' not in repr(settings) and 'content-private.example' not in repr(settings)


def test_factory_preserves_existing_hard_budget_caps():
    model = build_content_model(calls=90, tokens=90000, configuration=configuration())
    assert model.max_calls == 12 and model.max_tokens == 6000


@pytest.mark.parametrize('name', ['quality-model', 'gpt-6-astra', 'gpt-6.1-sol'])
def test_explicit_role_surfaces_provider_failure_without_retrying_on_original_service(monkeypatch, name):
    import app.llm as transport
    sent = []

    def handle(request):
        sent.append((request.url.host, request.headers.get('authorization')))
        return httpx.Response(503, json={'error': 'synthetic provider failure'})

    client = httpx.AsyncClient
    monkeypatch.setattr(transport.httpx, 'AsyncClient', lambda **kwargs:
        client(transport=httpx.MockTransport(handle), **kwargs))
    model = build_content_model(configuration=configuration(content_base_url='https://content.example/v1',
        content_api_key='content-synthetic', content_model=name))
    with pytest.raises(ModelCallError) as caught:
        asyncio.run(model._json('synthetic system', {'task': 'synthetic'}))
    assert caught.value.code == 'model_http_error' and caught.value.http_status == 503
    assert sent == [('content.example', 'Bearer content-synthetic')]
    assert model._calls == 1 and len(model.metrics) == 1
    assert 'content-synthetic' not in str(model.metrics)


@pytest.mark.parametrize('name', ['gpt-6-astra', 'gpt-6.1-sol'])
def test_explicit_reasoning_content_wire_has_low_effort_and_exact_existing_token_budget(name):
    model = build_content_model(calls=6, tokens=4100, timeout=81, configuration=configuration(
        content_base_url='https://content.example/v1', content_api_key='content-synthetic', content_model=name))
    assert isinstance(model, ContentLLM)
    body = model._chat_body('system', '{"task":"realistic"}', .95)
    assert body == {'model': name,
        'messages': [{'role': 'system', 'content': 'system'}, {'role': 'user', 'content': '{"task":"realistic"}'}],
        'response_format': {'type': 'json_object'}, 'reasoning_effort': 'low',
        'max_completion_tokens': 4100}
    assert model.max_calls == 6 and model.timeout == 81 and model._calls == 0


def test_original_llm_even_with_reasoning_model_name_keeps_the_original_wire():
    cfg = configuration()
    cfg.llm_model = 'gpt-6.1-sol'
    model = build_content_model(tokens=3200, configuration=cfg)
    assert type(model) is OpenAICompatibleLLM
    assert model._chat_body('system', '{}', .8) == {
        'model': 'gpt-6.1-sol', 'messages': [{'role': 'system', 'content': 'system'}, {'role': 'user', 'content': '{}'}],
        'response_format': {'type': 'json_object'}, 'temperature': .8, 'max_tokens': 3200}


def test_other_explicit_content_model_keeps_original_wire():
    model = build_content_model(tokens=1800, configuration=configuration(content_base_url='https://content.example/v1',
        content_api_key='content-synthetic', content_model='another-model'))
    assert type(model) is OpenAICompatibleLLM
    body = model._chat_body('system', '{}', .8)
    assert body['temperature'] == .8 and body['max_tokens'] == 1800
    assert 'reasoning_effort' not in body and 'max_completion_tokens' not in body


def test_reasoning_content_actual_transport_uses_adapted_wire_with_one_call(monkeypatch):
    import app.llm as transport
    sent = []

    def handle(request):
        sent.append(json.loads(request.content))
        return httpx.Response(200, json={'choices': [{'finish_reason': 'stop',
            'message': {'content': '{"ok":true}'}}]})

    client = httpx.AsyncClient
    monkeypatch.setattr(transport.httpx, 'AsyncClient', lambda **kwargs:
        client(transport=httpx.MockTransport(handle), **kwargs))
    model = build_content_model(calls=2, tokens=2800, configuration=configuration(
        content_base_url='https://content.example/v1', content_api_key='content-synthetic', content_model='gpt-6.1-sol'))
    assert asyncio.run(model._json('system', {'task': 'synthetic'})) == {'ok': True}
    assert len(sent) == model._calls == 1
    assert sent[0]['reasoning_effort'] == 'low' and sent[0]['max_completion_tokens'] == 2800
    assert 'temperature' not in sent[0] and 'max_tokens' not in sent[0]


def test_revision_entry_reports_partial_role_without_exposing_credentials(monkeypatch):
    from fastapi import HTTPException
    from app.config import settings
    from app.creative_agent_routes import model
    monkeypatch.setattr(settings, 'content_base_url', 'https://private-endpoint.example/v1')
    monkeypatch.setattr(settings, 'content_api_key', 'CANARY_CONTENT_PRIVATE')
    monkeypatch.setattr(settings, 'content_model', '')
    with pytest.raises(HTTPException) as caught:
        model()
    assert caught.value.status_code == 503
    assert 'CANARY_CONTENT_PRIVATE' not in caught.value.detail
    assert 'private-endpoint' not in caught.value.detail
