"""Platform identities are verified transport identifiers, never display names."""
import hashlib
import json
import secrets
import time
from contextlib import contextmanager
from uuid import uuid4

from fastapi import HTTPException
from sqlalchemy import delete, select, update
from sqlalchemy.exc import IntegrityError

from app.tenancy import require_context, set_context, reset_context
from app.tenancy.auth import context_from_session, new_session, session_row, token_hash
from app.tenancy.database import db_connection, sessions
from .tables import bindings, codes, threads


def digest(*parts):
    return hashlib.sha256(json.dumps(parts, ensure_ascii=False, separators=(',', ':')).encode()).hexdigest()


def binding_context(row):
    if not row or not row['active']:
        raise HTTPException(401, '请在私聊中重新关联你的工作空间')
    session = session_row(row['session_hash'], hashed=True)
    if not session or session['user_id'] != row['user_id']:
        raise HTTPException(401, '请重新关联你的工作空间')
    context = context_from_session({**session, 'tenant_id': row['tenant_id'], 'store_id': row['store_id']})
    if not context or context.store_id != row['store_id']:
        raise HTTPException(401, '门店权限已更新，请重新关联')
    return context


@contextmanager
def as_binding(row):
    token = set_context(binding_context(row))
    try:
        yield
    finally:
        reset_context(token)


def find_binding(identifier):
    with db_connection() as connection:
        row = connection.execute(select(bindings).where(bindings.c.id == identifier)).mappings().first()
    return dict(row) if row else None


def resolve_identity(event, *, create_guest=True):
    identity = digest(event.channel, event.connection_id, event.sender_id)
    with db_connection() as connection:
        row = connection.execute(select(bindings).where(bindings.c.identity_hash == identity)).mappings().first()
    if row:
        binding_context(row)
        return dict(row)
    if not create_guest:
        return None
    # Each previously unseen authenticated platform identity starts independently.
    # A race loser can leave an inaccessible trial, but cannot acquire the winner's
    # identity or reuse its private data; the unique identity index is authoritative.
    from app.trial_onboarding import provision_trial, TRIAL_SECONDS
    uid, tenant, store = provision_trial()
    raw = new_session(uid, tenant, store, seconds=TRIAL_SECONDS)
    now = time.time()
    values = dict(id=uuid4().hex, channel=event.channel, connection_id=event.connection_id,
        identity_hash=identity, user_id=uid, tenant_id=tenant, store_id=store,
        session_hash=token_hash(raw), active=True, created_at=now, last_seen_at=now)
    try:
        with db_connection() as connection:
            connection.execute(bindings.insert().values(**values))
        return values
    except IntegrityError:
        with db_connection() as connection:
            connection.execute(delete(sessions).where(sessions.c.token_hash == values['session_hash']))
            row = connection.execute(select(bindings).where(bindings.c.identity_hash == identity)).mappings().one()
        binding_context(row)
        return dict(row)


def create_code(channel, *, purpose='binding', conversation_id=None, seconds=600):
    context = require_context()
    if not context.session_hash:
        raise HTTPException(401, '请先登录工作空间')
    live = session_row(context.session_hash, hashed=True)
    if not live:
        raise HTTPException(401, '请重新登录工作空间')
    if conversation_id:
        from app.gtm_agent.service import get_conversation
        get_conversation(conversation_id)
    # Short typeable binding codes carry ~60 bits and expire in ten minutes.
    code = secrets.token_hex(8) if purpose == 'binding' else secrets.token_urlsafe(32)
    now = time.time()
    values = dict(digest=token_hash(code), purpose=purpose, channel=channel,
        user_id=context.user_id, tenant_id=context.tenant_id, store_id=context.store_id,
        session_hash=context.session_hash, conversation_id=conversation_id,
        created_at=now, expires_at=now+seconds)
    with db_connection() as connection:
        connection.execute(delete(codes).where(codes.c.expires_at < now))
        connection.execute(codes.insert().values(**values))
    return {'code': code, 'expires_at': values['expires_at']}


def code_row(code, purpose, channel=None):
    if len(code) > 256:
        raise HTTPException(410, '连接已过期，请重新打开')
    query = select(codes).where(codes.c.digest == token_hash(code), codes.c.purpose == purpose,
        codes.c.used_at.is_(None), codes.c.expires_at > time.time())
    if channel:
        query = query.where(codes.c.channel == channel)
    with db_connection() as connection:
        row = connection.execute(query).mappings().first()
    if not row:
        raise HTTPException(410, '连接已过期或已使用，请重新生成')
    session = session_row(row['session_hash'], hashed=True)
    if not session or session['user_id'] != row['user_id']:
        raise HTTPException(410, '原工作空间已退出，请重新生成连接')
    context = context_from_session({**session, 'tenant_id': row['tenant_id'], 'store_id': row['store_id']})
    if not context or context.store_id != row['store_id']:
        raise HTTPException(410, '工作权限已更新，请重新生成连接')
    return dict(row), context


def consume_code(connection, row):
    result = connection.execute(update(codes).where(codes.c.digest == row['digest'],
        codes.c.used_at.is_(None), codes.c.expires_at > time.time()).values(used_at=time.time()))
    if result.rowcount != 1:
        raise HTTPException(410, '连接已过期或已使用')


def bind_code(event, code):
    if event.audience != 'private':
        raise HTTPException(403, '请在私聊中关联工作空间')
    row, context = code_row(code, 'binding', event.channel)
    identity = digest(event.channel, event.connection_id, event.sender_id)
    from app.tenancy.auth import guest_state
    from app.trial_onboarding import TRIAL_SECONDS
    duration = TRIAL_SECONDS if guest_state(context.user_id, context.tenant_id)['is_guest'] else 7*86400
    raw = new_session(context.user_id, context.tenant_id, context.store_id, seconds=duration)
    now = time.time()
    values = dict(id=uuid4().hex, channel=event.channel, connection_id=event.connection_id,
        identity_hash=identity, user_id=context.user_id, tenant_id=context.tenant_id,
        store_id=context.store_id, session_hash=token_hash(raw), active=True, created_at=now, last_seen_at=now)
    try:
        with db_connection() as connection:
            consume_code(connection, row)
            previous = connection.execute(select(bindings).where(bindings.c.identity_hash == identity)).mappings().first()
            if previous:
                # Old inbox/thread records retain their original binding ID and
                # cannot be delivered under the replacement identity's permissions.
                connection.execute(update(bindings).where(bindings.c.id == previous['id']).values(
                    active=False, identity_hash='retired:'+previous['id']))
                connection.execute(delete(sessions).where(sessions.c.token_hash == previous['session_hash']))
            connection.execute(bindings.insert().values(**values))
            if row['conversation_id']:
                connection.execute(threads.insert().values(id=thread_key(values, event),
                    binding_id=values['id'], conversation_id=row['conversation_id'], updated_at=now))
        return values
    except Exception:
        with db_connection() as connection:
            connection.execute(delete(sessions).where(sessions.c.token_hash == values['session_hash']))
        raise


def thread_key(binding, event):
    return digest(binding['id'], event.audience, event.chat_id, event.thread_id)
