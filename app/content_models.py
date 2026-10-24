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


def build_content_model(*, calls=4, tokens=2400, timeout=60, configuration=None,
                        concept_preflight=False):
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
    result = client(base, key, model, timeout=timeout, max_calls=calls, max_tokens=tokens)
    # Interactive creation keeps divergent exploration and the mandatory review
    # of the actual draft. Reviewing a proposed concept before the same review
    # of the completed draft adds a serial judgement (and sometimes a second
    # exploration), with no publishable result yet. Batch callers can opt in.
    result.concept_preflight_enabled = bool(concept_preflight)
    return result


def content_timing_snapshot(model):
    """Bounded numeric diagnostics, never prompts, responses or credentials."""
    stages = {'concept', 'concept_preflight', 'draft', 'repair', 'reselection',
              'fact_review', 'marketing_review', 'model'}
    rows = []
    for metric in getattr(model, 'metrics', [])[:12]:
        if not isinstance(metric, dict):
            continue
        stage = metric.get('stage')
        stage = stage if stage in stages else 'model'
        duration = metric.get('duration_ms', 0)
        duration = duration if isinstance(duration, int) and not isinstance(duration, bool) else 0
        rows.append({'stage': stage, 'duration_ms': max(0, min(duration, 600000))})
    return {'completed_calls': len(rows), 'model_duration_ms': sum(row['duration_ms'] for row in rows),
            'calls': rows}
