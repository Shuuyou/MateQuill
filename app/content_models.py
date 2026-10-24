"""Explicit content-model role using the existing bounded JSON client.

All three CONTENT fields form one connection. Empty fields preserve the current
LLM service; partial configuration never borrows credentials from another role.
This factory performs no requests and adds no retry or provider fallback.
"""
from .config import settings
from .llm import ModelCallError, OpenAICompatibleLLM


REASONING_CONTENT_MODELS = frozenset({'gpt-6-astra', 'gpt-6.1-sol'})


class ContentLLM(OpenAICompatibleLLM):
    """Explicit reasoning-content role; never changes the global client wire."""

    def _chat_body(self, system, serialized, temperature):
        body = super()._chat_body(system, serialized, temperature)
        if self.model in REASONING_CONTENT_MODELS:
            body.pop('temperature', None)
            body.pop('max_tokens', None)
            body['reasoning_effort'] = 'low'
            body['max_completion_tokens'] = self.max_tokens
        return body


def build_content_model(*, calls=4, tokens=2400, timeout=60, configuration=None):
    """Return one client with the caller's existing workflow budgets."""
    configuration = settings if configuration is None else configuration
    explicit = tuple(getattr(configuration, field, '') for field in
        ('content_base_url', 'content_api_key', 'content_model'))
    if any(not isinstance(value, str) for value in explicit):
        raise ModelCallError('invalid_content_model_configuration')
    explicit = tuple(value.strip() for value in explicit)
    if any(explicit):
        if not all(explicit):
            raise ModelCallError('incomplete_content_model_configuration')
        base, key, model = explicit
    else:
        base, key, model = (getattr(configuration, field, '') for field in
            ('llm_base_url', 'llm_api_key', 'llm_model'))
    client = ContentLLM if all(explicit) and model in REASONING_CONTENT_MODELS else OpenAICompatibleLLM
    return client(base, key, model, timeout=timeout, max_calls=calls, max_tokens=tokens)
