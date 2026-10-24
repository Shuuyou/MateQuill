from app.design.llm import ArtDirectionLLM
from app.llm import OpenAICompatibleLLM


def test_reasoning_art_director_uses_its_compatible_wire_parameters():
    client = ArtDirectionLLM('https://example.com/v1', 'fixture-key', 'gpt-6-astra', max_tokens=4800)
    body = client._chat_body('Return JSON', '{"task":"art direction"}', .55)
    assert body['reasoning_effort'] == 'low'
    assert body['max_completion_tokens'] == 9000
    assert 'temperature' not in body and 'max_tokens' not in body
    assert body['messages'][1]['content'] == '{"task":"art direction"}'


def test_art_adapter_does_not_change_legacy_writer_or_review_model_parameters():
    common = ('https://example.com/v1', 'fixture-key', 'gpt-4.1')
    director = ArtDirectionLLM(*common, max_tokens=2200)
    original = OpenAICompatibleLLM(*common, max_tokens=2200)
    assert director._chat_body('JSON', '{}', .65) == original._chat_body('JSON', '{}', .65)
    assert 'reasoning_effort' not in original._chat_body('JSON', '{}', .65)


def test_explicit_visual_reviewer_wins_with_known_multimodal_fallback(monkeypatch):
    from app import design_service
    monkeypatch.setattr(design_service.settings, 'image_api_key', 'fixture-key')
    monkeypatch.setattr(design_service.settings, 'image_review_model', 'gpt-4o')
    monkeypatch.setattr(design_service.settings, 'design_text_model', 'gpt-6-astra')
    assert design_service._design_review_model() == 'gpt-4o'
    assert design_service._copy_auditor().model == 'gpt-4o'
    monkeypatch.setattr(design_service.settings, 'image_review_model', '')
    assert design_service._design_review_model() == 'gpt-6-astra'
    monkeypatch.setattr(design_service.settings, 'design_text_model', 'custom-text-only')
    assert design_service._design_review_model() == ''
    monkeypatch.setattr(design_service.settings, 'image_review_model', 'gpt-4o')
    assert design_service._design_review_model() == 'gpt-4o'
