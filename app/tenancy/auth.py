"""Argon2 passwords, opaque server-side sessions and revocable membership."""
import hashlib
import os
import secrets
import time
from threading import BoundedSemaphore
from urllib.parse import urlparse
from uuid import uuid4

from argon2 import PasswordHasher
from argon2.exceptions import VerificationError, InvalidHashError
from fastapi import HTTPException
from sqlalchemy import delete, select, update
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import JSONResponse

from .context import TenantContext, _context
from .database import db_connection, memberships, sessions, tenants, users

COOKIE_NAME = 'matequill_session'
SESSION_SECONDS = 7 * 24 * 60 * 60
passwords = PasswordHasher()
_DUMMY_HASH = passwords.hash(secrets.token_urlsafe(32))
_PASSWORD_SLOTS = BoundedSemaphore(4)


def token_hash(token):
    return hashlib.sha256(token.encode()).hexdigest()


def verify_password(password, encoded):
    if not _PASSWORD_SLOTS.acquire(timeout=2):
        raise HTTPException(429, '登录请求较多，请稍后重试')
    try:
        return passwords.verify(encoded, password)
    except (VerificationError, InvalidHashError):
        return False
    finally:
        _PASSWORD_SLOTS.release()


def password_hash(password):
    if not 12 <= len(password) <= 128:
        raise HTTPException(422, '密码请使用 12 至 128 个字符')
    if not _PASSWORD_SLOTS.acquire(timeout=2):
        raise HTTPException(429, '账号请求较多，请稍后重试')
    try:
        return passwords.hash(password)
    finally:
        _PASSWORD_SLOTS.release()


def normalize_email(value):
    value = value.strip().lower()
    if len(value) > 254 or '@' not in value or not value.split('@')[0] or '.' not in value.rsplit('@', 1)[1]:
        raise HTTPException(422, '请填写有效邮箱')
    return value


def membership_rows(user_id, tenant_id=None):
    query = select(memberships, tenants.c.name.label('tenant_name')).join(
        tenants, tenants.c.id == memberships.c.tenant_id).where(
        memberships.c.user_id == user_id, memberships.c.active.is_(True))
    if tenant_id:
        query = query.where(memberships.c.tenant_id == tenant_id)
    with db_connection() as connection:
        return [dict(row) for row in connection.execute(query.order_by(memberships.c.created_at)).mappings()]


def context_from_session(row):
    rows = membership_rows(row['user_id'], row['tenant_id'])
    if not rows:
        return None
    grants = tuple((item['role'], item['store_id']) for item in rows)
    all_stores = any(scope is None for _, scope in grants)
    available = tuple(sorted({scope for _, scope in grants if scope is not None}))
    selected = row['store_id']
    if selected and not all_stores and selected not in available:
        selected = available[0] if len(available) == 1 else None
    roles = tuple(sorted({role for role, scope in grants if scope is None or scope == selected}))
    return TenantContext(user_id=row['user_id'], tenant_id=row['tenant_id'], store_id=selected,
        roles=roles, store_ids=available, all_stores=all_stores, grants=grants, session_hash=row['token_hash'])


def session_row(token_or_hash, *, hashed=False):
    if not token_or_hash or len(token_or_hash) > 512:
        return None
    digest = token_or_hash if hashed else token_hash(token_or_hash)
    with db_connection() as connection:
        row = connection.execute(select(sessions).join(users, users.c.id == sessions.c.user_id).where(
            sessions.c.token_hash == digest, sessions.c.expires_at > time.time(), users.c.active.is_(True))).mappings().first()
    return dict(row) if row else None


def resolve_request_context(request):
    row = session_row(request.cookies.get(COOKIE_NAME))
    return context_from_session(row) if row else None


def refresh_context(context):
    if context.is_legacy:
        return context
    row = session_row(context.session_hash, hashed=True)
    if not row or row['user_id'] != context.user_id:
        return None
    # Workspace navigation is not revocation: an already queued task keeps its
    # original enterprise and store while checking that membership is still live.
    refreshed = context_from_session({**row, 'tenant_id': context.tenant_id, 'store_id': context.store_id})
    if refreshed is None:
        return None
    # Jobs retain their original store, never silently switch to a newly selected workspace.
    if context.store_id and not refreshed.all_stores and context.store_id not in refreshed.store_ids:
        return None
    from dataclasses import replace
    return replace(refreshed, store_id=context.store_id,
                   roles=tuple(sorted({role for role, scope in refreshed.grants if scope is None or scope == context.store_id})))


def new_session(user_id, tenant_id, store_id=None):
    token = secrets.token_urlsafe(48)
    now = time.time()
    with db_connection() as connection:
        connection.execute(delete(sessions).where(sessions.c.expires_at < now))
        connection.execute(sessions.insert().values(token_hash=token_hash(token), user_id=user_id,
            tenant_id=tenant_id, store_id=store_id, expires_at=now + SESSION_SECONDS, created_at=now))
    return token


def set_session_cookie(response, token, request):
    secure = os.getenv('MATEQUILL_COOKIE_SECURE', '0') == '1' or request.url.scheme == 'https'
    response.set_cookie(COOKIE_NAME, token, httponly=True, secure=secure,
                        samesite='lax', max_age=SESSION_SECONDS, path='/')


def me_payload(request):
    row = session_row(request.cookies.get(COOKIE_NAME))
    if not row:
        return {'authenticated': False, 'user': None, 'workspace': None, 'memberships': []}
    context = context_from_session(row)
    rows = membership_rows(row['user_id'])
    with db_connection() as connection:
        user = connection.execute(select(users.c.id, users.c.email, users.c.name).where(users.c.id == row['user_id'])).mappings().one()
    selected = next((item for item in rows if context and item['tenant_id'] == context.tenant_id), None)
    workspace = None if not context else dict(tenant_id=context.tenant_id,
        tenant_name=selected['tenant_name'], store_id=context.store_id, roles=list(context.roles),
        store_ids=list(context.store_ids), all_stores=context.all_stores)
    return {'authenticated': True, 'user': dict(user), 'workspace': workspace,
            'memberships': [{key: item[key] for key in ('id', 'tenant_id', 'tenant_name', 'store_id', 'role')} for item in rows]}


def validate_origin(request):
    origin = request.headers.get('origin')
    if not origin:
        raise HTTPException(403, '请从工作台发起操作')
    parsed = urlparse(origin)
    if parsed.scheme not in {'http', 'https'} or parsed.username or parsed.password:
        raise HTTPException(403, '请求来源无效')
    configured = os.getenv('MATEQUILL_ALLOWED_ORIGINS', 'http://127.0.0.1:3003,http://localhost:3003,http://127.0.0.1:3001,http://localhost:3001')
    allowed = {value.strip().rstrip('/') for value in configured.split(',') if value.strip()}
    allowed.add(str(request.base_url).rstrip('/'))
    if origin.rstrip('/') not in allowed:
        raise HTTPException(403, '请求来源无效')


class TenantContextMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request, call_next):
        public = request.url.path in {'/health', '/api/auth/login', '/api/auth/me', '/api/auth/accept-invitation'}
        is_api = request.url.path.startswith('/api/')
        context = None
        try:
            context = resolve_request_context(request)
            if is_api and not public and context is None:
                return JSONResponse({'detail': '请先登录工作空间'}, status_code=401,
                                    headers={'Cache-Control': 'private, no-store'})
            expected_workspace = request.headers.get('x-matequill-workspace')
            if (is_api and context and not context.is_legacy and expected_workspace is not None
                    and not request.url.path.startswith('/api/auth/')
                    and expected_workspace != context.tenant_id + ':' + (context.store_id or '')):
                # A stale browser tab may not silently create in another tab's store.
                # This header is only a concurrency precondition, never identity.
                return JSONResponse({'detail': '工作空间已在其他页面切换，请刷新后继续',
                                     'code': 'workspace_changed'}, status_code=409,
                                    headers={'Cache-Control': 'private, no-store'})
            if is_api and request.method not in {'GET', 'HEAD', 'OPTIONS'} and not (context and context.is_legacy):
                validate_origin(request)
            token = _context.set(context)
            try:
                response = await call_next(request)
            finally:
                _context.reset(token)
            if is_api:
                response.headers['Cache-Control'] = 'private, no-store'
                response.headers['Vary'] = 'Cookie'
            return response
        except HTTPException as exc:
            return JSONResponse({'detail': exc.detail}, status_code=exc.status_code,
                                headers={'Cache-Control': 'private, no-store'})


def install_tenancy(app):
    app.add_middleware(TenantContextMiddleware)
