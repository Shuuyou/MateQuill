from copy import deepcopy
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
