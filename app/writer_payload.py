"""Lossless wire-only deduplication; source evidence and stored state stay intact."""
from copy import deepcopy


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
    if isinstance(marketing, dict):
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
    if references:
        value['context_refs'] = {'instruction': '这些字段与所指向的正文完全相同，按path读取对应内容；未删减要求、原文或限定。',
                                 'fields': references}
    return value
