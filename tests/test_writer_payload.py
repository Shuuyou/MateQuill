from copy import deepcopy
import json
import asyncio
from app.writer_payload import compact_writer_payload


def test_exact_duplicate_context_is_losslessly_referenced_not_truncated():
    evidence = {'evidence': [{'id': 'actual', 'quote': '完整原文和限定' * 500}]}
    snapshot = {'version': 1, 'facts': ['actual']}
    task = {'product': '产品', 'requirements': '保留用户的全部要求', 'scene': '当前场景'}
    original = {'task': task, 'strategy': {'strategy_snapshot': snapshot},
        'local_editorial_context': evidence, 'marketing_context': {'local_editorial_context': evidence},
        'platform_playbook': {'opening': '自然表达'}, 'creative_brief': {
            'task_context': {'requirements': task['requirements'], 'scene': task['scene']},
            'strategy_snapshot': snapshot, 'playbook': {'opening': '自然表达'},
            'local_editorial_context': evidence},
        'output_contract': {'images': {'items': {'enum': ['full-asset-id']}}}}
    saved = deepcopy(original)
    result = compact_writer_payload(original)
    assert original == saved
    assert len(result['context_refs']['fields']) == 5
    assert result['local_editorial_context'] == evidence
    assert result['output_contract'] == original['output_contract']
    restored = deepcopy(result)
    for destination, reference in restored.pop('context_refs')['fields'].items():
        canonical = restored
        for key in reference['path'].split('.'): canonical = canonical[key]
        replacement = ({key: canonical[key] for key in reference['keys']}
                       if 'keys' in reference else canonical)
        parent_name, key = destination.split('.')
        restored[parent_name][key] = deepcopy(replacement)
    assert restored == original


def test_meaningfully_different_context_is_never_removed():
    original = {'task': {'requirements': '用户本轮要求'}, 'local_editorial_context': {'scene': '新场景'},
        'creative_brief': {'local_editorial_context': {'scene': '旧场景'},
                           'task_context': {'requirements': '另一组条件'}}}
    assert compact_writer_payload(original) == original


def _restore_references(compact):
    restored = deepcopy(compact)
    def at(path):
        node = restored
        for key in path.split('.'):
            node = node[key]
        return node
    for destination, reference in restored.pop('context_refs')['fields'].items():
        canonical = at(reference['path'])
        replacement = ({key: canonical[key] for key in reference['keys']}
                       if 'keys' in reference else canonical)
        parent, key = destination.rsplit('.', 1)
        at(parent)[key] = deepcopy(replacement)
    return restored


def test_repair_execution_duplicates_are_losslessly_referenced():
    large = lambda name: {'instruction': name + '完整保留本地场景、身份、表达及限定' * 200}
    local, voice, concept = large('local'), large('voice'), large('concept')
    account, culture = large('account'), large('culture')
    marketing = {'local_editorial_context': local, 'policies': large('policy')}
    original = {'task': {'requirements': '用户本轮全部要求'},
        'editorial_voice': voice, 'local_editorial_context': local, 'editorial_concept': concept,
        'marketing_context': marketing, 'creative_brief': {'account_voice': account, 'cultural_context': culture},
        'strategy': {'repair': True, 'marketing_context': marketing,
            'creative_execution': {'editorial_voice': voice, 'local_editorial_context': local,
                'account_voice': account, 'cultural_context': culture,
                'exploration': {'selected_concept': concept}},
            'previous_draft': {'title': '保持原钩子', 'blocks': [{'text': '原稿完整文字'}]},
            'required_fixes': ['仅修明确错误，不改其他内容']}}
    saved = deepcopy(original)
    compact = compact_writer_payload(original)
    assert original == saved
    assert _restore_references(compact) == original
    assert compact['strategy']['required_fixes'] == original['strategy']['required_fixes']
    assert compact['strategy']['previous_draft'] == original['strategy']['previous_draft']
    assert len(json.dumps(compact, ensure_ascii=False)) < len(json.dumps(original, ensure_ascii=False)) * .7


def test_new_reference_pairs_keep_different_and_small_guidance():
    original = {'editorial_voice': {'voice': '新声音'}, 'creative_brief': {'account_voice': {'kind': '店员'}},
                'strategy': {'creative_execution': {'editorial_voice': {'voice': '旧声音'},
                    'account_voice': {'kind': '店员'}}}}
    assert compact_writer_payload(original) == original


def test_writer_records_actual_compacted_wire_sizes_without_content():
    from app.llm import OpenAICompatibleLLM
    model = OpenAICompatibleLLM(api_key='synthetic')
    guidance = {'scene': '完整可还原的场景指令' * 150}
    payload = {'creative_brief': {'local_editorial_context': guidance},
               'local_editorial_context': guidance, 'output_contract': {}}
    captured = []
    async def send(system, value, temperature):
        captured.append(value)
        return {'ok': True}
    model._json_once = send
    assert asyncio.run(model._json('synthetic instruction', payload)) == {'ok': True}
    assert model.last_writer_payload_stats == {
        'original_bytes': len(json.dumps(payload, ensure_ascii=False).encode('utf8')),
        'wire_bytes': len(json.dumps(captured[0], ensure_ascii=False).encode('utf8'))}
    assert model.last_writer_payload_stats['wire_bytes'] < model.last_writer_payload_stats['original_bytes']


def test_timing_exposes_only_bounded_writer_size_counters():
    from app.content_models import content_timing_snapshot
    class Model:
        metrics = [{'stage': 'draft', 'duration_ms': 3, 'writer_payload': {
            'original_bytes': 90000, 'wire_bytes': 48000, 'content': 'not a metric'}},
            {'stage': 'fact_review', 'duration_ms': 2, 'writer_payload': {'original_bytes': 90000}},
            {'stage': 'repair', 'duration_ms': 1, 'writer_payload': {'original_bytes': True, 'wire_bytes': -1}}]
    rows = content_timing_snapshot(Model())['calls']
    assert rows[0] == {'stage': 'draft', 'duration_ms': 3, 'writer_original_bytes': 90000, 'writer_wire_bytes': 48000}
    assert rows[1] == {'stage': 'fact_review', 'duration_ms': 2}
    assert rows[2] == {'stage': 'repair', 'duration_ms': 1}
