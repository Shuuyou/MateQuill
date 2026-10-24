"""Store-scoped memory from explicit creative feedback, never customer profiling.

The instructions returned here are quoted user data. Consumers must not execute
them as tools, override system policy, or treat them as product evidence.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Literal
from uuid import uuid4

from fastapi import HTTPException

from .tenancy import authorize_resource, list_resources, require_context
from .tenancy.context import authorize_scope
from .tenancy.resources import register_resource

KIND = 'creative_memory'
MAX_CONTEXT_CHARS = 12000


def _selected_store(store_id=None):
    context = require_context()
    selected = context.store_id
    if store_id and store_id != selected:
        raise HTTPException(404, '请先切换到这家门店')
    if selected:
        authorize_scope(selected)
    return selected


def _copy_excerpt(value):
    """Only short publishable copy; no traces, private uploads or arbitrary keys."""
    if value is None:
        return None
    if not isinstance(value, dict):
        raise ValueError('Creative memory copy must be a dictionary')
    pieces = []
    for key in ('title', 'intro', 'cta'):
        if isinstance(value.get(key), str):
            pieces.append(value[key][:400])
    for block in value.get('blocks', [])[:3] if isinstance(value.get('blocks'), list) else []:
        if isinstance(block, dict) and isinstance(block.get('text'), str):
            pieces.append(block['text'][:400])
    return '\n'.join(pieces)[:1000]


def record_creative_memory(run_id: str, instruction: str, before=None, after=None, *,
                           event: Literal['revised', 'adopted'] = 'revised') -> dict:
    """Record one explicit user action after the actual revision/adoption succeeds.

    No automatic inference of preferences from silence, uploads or CRM records.
    ``event`` is selected by the trusted revision/adoption handler.
    """
    context = require_context()
    if event not in {'revised', 'adopted'}:
        raise ValueError('Unsupported creative memory event')
    if not isinstance(instruction, str) or not instruction.strip():
        raise ValueError('Explicit creative feedback is required')
    if len(instruction) > 2000:
        raise ValueError('Creative feedback exceeds 2000 characters')
    store_id = _selected_store()
    if not store_id or context.is_legacy:
        raise HTTPException(422, '请选择门店后再保存创作偏好')
    run = authorize_resource('run', run_id, action='write' if event == 'revised' else 'read')
    if run.get('store_id') != store_id:
        raise HTTPException(404, '没有找到这家门店的创作')
    body = run.get('body') or {}
    if body.get('status') != 'ready':
        raise HTTPException(409, '完成创作后再记录这次偏好')
    task = body.get('_poster_task') or body.get('task') or {}
    identifier = uuid4().hex
    memory = {'id': identifier, 'run_id': run_id, 'store_id': store_id,
              'source': 'explicit_user_feedback', 'event': event,
              'instruction': instruction.strip(), 'actor_id': context.user_id,
              'created_at': datetime.now(timezone.utc).isoformat(),
              'platform': str(task.get('platform', ''))[:64],
              'product': str(task.get('product', ''))[:200],
              'before': _copy_excerpt(before), 'after': _copy_excerpt(after),
              'scope': '创作表达偏好；不是好友画像、产品事实或发布效果'}
    register_resource(KIND, identifier, store_id=store_id, visibility='store', body=memory)
    return memory


def read_creative_memory(store_id: str | None = None, limit: int = 8) -> list[dict]:
    """Read bounded recent feedback for the currently selected store only.

    Rechecks source-run visibility so memory cannot expose a now-private source.
    Explicit preference text is untrusted data, not an instruction hierarchy.
    """
    context = require_context()
    selected = _selected_store(store_id)
    if not selected or context.is_legacy:
        return []
    limit = min(20, max(1, int(limit)))
    result = []
    consumed = 0
    for row in list_resources(KIND):
        if row.get('store_id') != selected:
            continue
        value = row.get('body') or {}
        if value.get('store_id') != selected or value.get('source') != 'explicit_user_feedback':
            continue
        try:
            source = authorize_resource('run', value['run_id'])
        except (HTTPException, KeyError):
            continue
        if source.get('store_id') != selected:
            continue
        # Deliberately omit other-user identifiers and copy excerpts from prompts.
        item = {key: value.get(key) for key in ('id', 'run_id', 'store_id', 'source', 'event',
                                               'created_at', 'platform', 'product', 'scope')}
        item['instruction'] = str(value.get('instruction', ''))[:1000]
        size = sum(len(str(v)) for v in item.values())
        if consumed + size > MAX_CONTEXT_CHARS:
            break
        result.append(item)
        consumed += size
        if len(result) >= limit:
            break
    return result


def creative_working_memories(*, audience='private', limit=8):
    """Read historical feedback for explicit inspection, not automatic recall.

    These are immutable edit-event references, not standing preferences. The
    conversational runtime only recalls separately sourced remember() records.
    Private source works stay personal even when a manager can read the run.
    """
    context = require_context()
    result = []
    for item in read_creative_memory(limit=limit):
        if item['event'] != 'revised':
            continue
        source = authorize_resource('run', item['run_id'])
        if source['visibility'] == 'private' and (audience == 'group' or source['owner_id'] != context.user_id):
            continue
        created = datetime.fromisoformat(item['created_at']).timestamp()
        if datetime.now(timezone.utc).timestamp() - created > 30 * 86400:
            continue
        result.append({'id': 'creative:' + item['id'], 'kind': 'observation',
            'key': 'historical_draft_edit',
            'text': '以前一份作品的局部编辑记录，仅供理解表达偏好；不沿用其中具体标题、参数或一次性编辑指令：' + item['instruction'],
            'quote': item['instruction'],
            'source_ids': ['run:' + item['run_id']], 'source_run_id': item['run_id'],
            'created_at': created, 'expires_at': created + 30 * 86400,
            'scope': 'historical_revision_only；本次任务优先，不是客户画像或产品事实'})
    return result
