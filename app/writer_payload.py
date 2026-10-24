"""Lossless wire-only deduplication; source evidence and stored state stay intact."""
from copy import deepcopy
import json


def compact_writer_payload(payload):
    value = deepcopy(payload)
    references = {}
    brief = value.get('creative_brief')
    if not isinstance(brief, dict):
        return value
    strategy = value.get('strategy')
    strategy = strategy if isinstance(strategy, dict) else {}
    pairs = [
        (brief, 'strategy_snapshot', strategy.get('strategy_snapshot'), 'strategy.strategy_snapshot'),
        (brief, 'playbook', value.get('platform_playbook'), 'platform_playbook'),
        (brief, 'local_editorial_context', value.get('local_editorial_context'), 'local_editorial_context'),
    ]
    marketing = value.get('marketing_context')
    # Keep this canonical context whole when an entire strategy copy can be
    # referenced; otherwise a later parent reference would depend on a child
    # that has itself already been removed.
    whole_marketing_duplicate = isinstance(marketing, dict) and strategy.get('marketing_context') == marketing
    if isinstance(marketing, dict) and not whole_marketing_duplicate:
        pairs.append((marketing, 'local_editorial_context', value.get('local_editorial_context'), 'local_editorial_context'))
    for parent, key, canonical, path in pairs:
        if canonical and key in parent and parent[key] == canonical:
            prefix = 'creative_brief' if parent is brief else 'marketing_context'
            references[prefix+'.'+key] = {'path': path}
            del parent[key]
    context = brief.get('task_context')
    task = value.get('task')
    if (isinstance(context, dict) and context and isinstance(task, dict)
            and all(key in task and task[key] == item for key, item in context.items())):
        references['creative_brief.task_context'] = {'path': 'task', 'keys': list(context)}
        del brief['task_context']
    # Repair requests also carry the previous execution and review context in
    # strategy. These records must remain in storage, but the writer need not
    # read identical account, local and editorial guidance twice. Explicit
    # canonical paths avoid generic deduplication of fact IDs or JSON schemas.
    def exact_reference(destination, canonical_path):
        def at(path):
            node = value
            for key in path.split('.'):
                if not isinstance(node, dict) or key not in node:
                    return None
                node = node[key]
            return node
        canonical = at(canonical_path)
        duplicate = at(destination)
        if not isinstance(canonical, (dict, list)) or not canonical or duplicate != canonical:
            return
        serialized = json.dumps(canonical, ensure_ascii=False, sort_keys=True)
        if (len(serialized.encode('utf8')) < 512 or
                json.dumps(duplicate, ensure_ascii=False, sort_keys=True) != serialized):
            return
        parent_path, key = destination.rsplit('.', 1)
        parent = at(parent_path)
        references[destination] = {'path': canonical_path}
        del parent[key]

    for destination, canonical_path in (
        ('strategy.marketing_context', 'marketing_context'),
        ('strategy.creative_execution.editorial_voice', 'editorial_voice'),
        ('strategy.creative_execution.local_editorial_context', 'local_editorial_context'),
        ('strategy.creative_execution.exploration.selected_concept', 'editorial_concept'),
        ('creative_brief.account_voice', 'strategy.creative_execution.account_voice'),
        ('creative_brief.cultural_context', 'strategy.creative_execution.cultural_context'),
    ):
        exact_reference(destination, canonical_path)
    if references:
        value['context_refs'] = {'instruction': '这些字段与所指向的正文完全相同，按path读取对应内容；未删减要求、原文或限定。',
                                 'fields': references}
    return value
