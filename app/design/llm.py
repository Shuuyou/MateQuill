"""Dedicated art-planning wire compatibility; the main copy writer is unchanged."""
from ..llm import OpenAICompatibleLLM


class ArtDirectionLLM(OpenAICompatibleLLM):
    def _chat_body(self, system, serialized, temperature):
        body = super()._chat_body(system, serialized, temperature)
        if self.model in {'gpt-6-astra', 'gpt-6.1-sol'}:
            body.pop('temperature', None)
            body.pop('max_tokens', None)
            body['reasoning_effort'] = 'low'
            body['max_completion_tokens'] = max(self.max_tokens, 9000)
        return body
