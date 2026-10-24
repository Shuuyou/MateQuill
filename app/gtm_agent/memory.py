"""Evidence-linked, revisable memories rather than a customer-profile form."""
import hashlib
import time

from fastapi import HTTPException
from sqlalchemy import select, update

from ..tenancy import require_context, list_resources, register_resource, service_update_body, transaction_context
from .repository import MEMORY
from ..tenancy.database import resources


def relevant_memories(*, audience='private', query='', limit=15):
    context = require_context()
    now = time.time()
    rows = []
    for row in list_resources(MEMORY):
        if row['store_id'] != context.store_id:
            continue
        if audience == 'group' and row['visibility'] not in {'store', 'tenant'}:
            continue
        if audience == 'private' and row['visibility'] == 'private' and row['owner_id'] != context.user_id:
            continue
        body = row['body'] or {}
        if body.get('superseded_by') or body.get('expires_at', 0) <= now:
            continue
        rows.append({**body, 'id': row['id']})
    # Draft edit events are an audit trail, not reusable instructions. Even a
    # historical-only label does not stop quoted scenes/parameters leaking into
    # a new task. Only separately sourced remember() records enter planning and
    # generation. The original events remain available through read_history.
    # Explicit corrections survive across tasks; retrieval remains bounded.
    words = [part for part in query.split() if len(part) > 1]
    return sorted(rows, key=lambda item: (item['kind'] == 'correction',
        sum(word in item['text'] for word in words), item['created_at']), reverse=True)[:limit]


def remember(turn, *, kind, key, text, quote='', evidence_ids=None, audience='private'):
    if kind not in {'observation', 'preference', 'correction', 'hypothesis'}:
        raise HTTPException(422, '记忆类型无效')
    text, key, quote = str(text).strip()[:700], str(key).strip()[:80], str(quote).strip()[:1000]
    if not text or not key:
        raise HTTPException(422, '请给记忆保留具体内容和主题')
    source_text = turn['text'] + '\n' + '\n'.join(turn.get('media_transcripts', []))
    known = set(turn.get('source_ids', []))
    if kind != 'hypothesis' and (not quote or quote not in source_text):
        raise HTTPException(422, '偏好和纠正需要对应本次实际表达')
    references = list(dict.fromkeys(evidence_ids or []))[:10]
    if any(source not in known for source in references):
        raise HTTPException(422, '记忆引用了尚未读取的来源')
    if kind == 'hypothesis' and not references:
        raise HTTPException(422, '策略判断需要关联已有线索')
    context = require_context()
    identifier = hashlib.sha256((turn['id']+':'+kind+':'+key+':'+text).encode()).hexdigest()[:40]
    visibility = 'store' if audience == 'group' else 'private'
    body = {'id': identifier, 'kind': kind, 'key': key, 'text': text, 'quote': quote,
        'source_turn_id': turn['id'], 'source_ids': references,
        'created_at': time.time(), 'expires_at': time.time()+ (90 if kind in {'preference','correction'} else 14)*86400,
        'superseded_by': None}
    with transaction_context() as connection:
        # Serialize replacement of the same store's memories, including when
        # two independent group conversations correct the same subject.
        if connection.dialect.name == 'sqlite':
            connection.execute(update(resources).where(resources.c.kind == 'store',
                resources.c.id == context.store_id).values(updated_at=resources.c.updated_at))
        else:
            connection.execute(select(resources.c.id).where(resources.c.kind == 'store',
                resources.c.id == context.store_id).with_for_update())
        existing = list_resources(MEMORY)
        for row in existing:
            prior = row['body'] or {}
            if row['id'] == identifier:
                return prior
            if (row['store_id'] == context.store_id and row['visibility'] == visibility
                    and (visibility != 'private' or row['owner_id'] == context.user_id)
                    and prior.get('key') == key and not prior.get('superseded_by')):
                service_update_body(MEMORY, row['id'], {**prior, 'superseded_by': identifier}, expected_version=row['version'])
        register_resource(MEMORY, identifier, body=body, visibility=visibility)
    return body
