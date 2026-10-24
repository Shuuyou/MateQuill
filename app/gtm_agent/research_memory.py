"""Recover the caller's recent real place research only after network failure.

This is server resource memory, never an imported answer or a fresh-search claim.
An ordinary empty search must remain empty. Queries and store location snapshots
must match exactly; discovery, rather than this cache, owns disambiguation.
"""
from copy import deepcopy
from datetime import datetime, timezone
import time

from fastapi import HTTPException
from sqlalchemy import select

from ..public_signals import public_url
from ..tenancy import authorize_resource, require_context
from ..tenancy.database import db_connection, resources

MAX_AGE = 86400


def _observed_timestamp(value):
    try:
        parsed = datetime.fromisoformat(str(value).replace('Z', '+00:00'))
        if parsed.tzinfo is None:
            return None
        return parsed.astimezone(timezone.utc).timestamp()
    except (TypeError, ValueError):
        return None


def recover_own_research(query, *, discovery, profile, now=None, audience='private'):
    """Return only a same-owner/store exact-query research snapshot, or None."""
    if discovery.get('network_state') != 'unavailable' or discovery.get('items'):
        return None
    context = require_context()
    if context.is_legacy or not context.store_id:
        return None
    now = time.time() if now is None else now
    # Select identifiers only, before reading any body: tenant administrators
    # must not accidentally consume another employee's private conversations.
    with db_connection() as connection:
        statement = select(resources.c.id).where(
            resources.c.kind == 'agent_conversation',
            resources.c.tenant_id == context.tenant_id,
            resources.c.store_id == context.store_id,
            resources.c.owner_id == context.user_id)
        if audience == 'group':
            statement = statement.where(resources.c.visibility.in_(['store', 'tenant']))
        identifiers = connection.execute(statement.order_by(resources.c.updated_at.desc()).limit(100)).scalars().all()
    candidates = []
    for identifier in identifiers:
        try:
            row = authorize_resource('agent_conversation', identifier)
        except HTTPException:
            continue
        if (row['tenant_id'], row['store_id'], row['owner_id']) != (
                context.tenant_id, context.store_id, context.user_id):
            continue
        if audience == 'group' and (row['visibility'] not in {'store', 'tenant'} or
                (row.get('body') or {}).get('audience') != 'group'):
            continue
        snapshot = (row.get('body') or {}).get('store_research') or {}
        if snapshot.get('store_id') != context.store_id:
            continue
        for research in snapshot.get('researches', [])[-3:]:
            if research.get('state') != 'found' or str(research.get('query') or '').strip() != query.strip():
                continue
            old_profile = research.get('profile') or {}
            if any(str(old_profile.get(key) or '').strip() != str(profile.get(key) or '').strip()
                    for key in ('name', 'location')):
                continue
            observed = _observed_timestamp(research.get('observed_at'))
            if observed is None or not 0 <= now - observed <= MAX_AGE:
                continue
            items = research.get('items') or []
            if not items or any(not isinstance(item, dict) or not public_url(str(item.get('url') or '')) for item in items):
                continue
            candidates.append((observed, identifier, research))
    if not candidates:
        return None
    _, identifier, selected = max(candidates, key=lambda row: row[0])
    return {**deepcopy(selected), 'cache_hit': True, 'research_origin': 'own_prior_research',
        'prior_conversation_id': identifier, 'fresh_attempt_failed': True,
        'network_state': 'unavailable', 'fresh_attempt_observed_at': discovery.get('observed_at')}
