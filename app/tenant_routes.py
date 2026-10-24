"""Authenticated workspaces, invitations and team administration."""
import json
import secrets
import time
from uuid import uuid4

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import delete, func, select, update, text
from sqlalchemy.exc import IntegrityError

from .tenancy import (authorize_resource, authorize_scope, audit, get_context,
                      require_context, require_role)
from .tenancy.auth import (COOKIE_NAME, SESSION_SECONDS, _DUMMY_HASH, context_from_session,
    me_payload, membership_rows, new_session, normalize_email, password_hash, passwords,
    set_session_cookie, token_hash, verify_password)
from .tenancy.database import (audit_events, db_connection, invitations, login_attempts,
                                memberships, sessions, tenants, users)

router = APIRouter(prefix='/api', tags=['identity-and-workspaces'])


class StrictInput(BaseModel):
    model_config = ConfigDict(extra='forbid')


class LoginInput(StrictInput):
    email: str = Field(min_length=3, max_length=254)
    password: str = Field(min_length=1, max_length=128)


class WorkspaceInput(StrictInput):
    tenant_id: str = Field(min_length=1, max_length=64)
    store_id: str | None = Field(default=None, max_length=120)


class InviteInput(StrictInput):
    email: str = Field(min_length=3, max_length=254)
    role: str = Field(pattern='^(admin|operator|regional_manager|store_manager|salesperson|reviewer)$')
    store_id: str | None = Field(default=None, max_length=120)


class AcceptInput(StrictInput):
    token: str = Field(min_length=20, max_length=256)
    name: str = Field(min_length=1, max_length=120)
    password: str = Field(min_length=12, max_length=128)


def _response_with_session(request, user_id, tenant_id, store_id=None, *, seconds=SESSION_SECONDS):
    old = request.cookies.get(COOKIE_NAME)
    if old:
        with db_connection() as connection:
            connection.execute(delete(sessions).where(sessions.c.token_hash == token_hash(old)))
    token = new_session(user_id, tenant_id, store_id, seconds=seconds)
    # Serialize from the newly created server session, without echoing its token.
    from .tenancy.auth import session_row
    row = session_row(token)
    context = context_from_session(row)
    rows = membership_rows(user_id)
    with db_connection() as connection:
        user = connection.execute(select(users.c.id, users.c.email, users.c.name).where(users.c.id == user_id)).mappings().one()
    chosen = next(item for item in rows if item['tenant_id'] == tenant_id)
    from .tenancy.auth import guest_state
    result = {'authenticated': True, 'user': dict(user), **guest_state(user_id, tenant_id),
        'workspace': {'tenant_id': tenant_id, 'tenant_name': chosen['tenant_name'],
                      'store_id': context.store_id, 'roles': list(context.roles),
                      'store_ids': list(context.store_ids), 'all_stores': context.all_stores},
        'memberships': [{key: item[key] for key in ('id', 'tenant_id', 'tenant_name', 'store_id', 'role')} for item in rows]}
    response = JSONResponse(result)
    set_session_cookie(response, token, request, seconds=seconds)
    return response


@router.post('/auth/guest')
def start_guest(request: Request):
    # Repeated clicks and reloads continue the current workspace without renewal.
    if get_context():
        return me_payload(request)
    from .trial_onboarding import provision_trial, TRIAL_SECONDS
    return _response_with_session(request, *provision_trial(), seconds=TRIAL_SECONDS)


class DemoInput(StrictInput):
    persona: str = Field(min_length=1, max_length=30)


@router.get('/auth/demo')
def demo_options():
    from .demo_workspaces import choices
    return {'accounts': choices()}


@router.post('/auth/demo')
def enter_demo(payload: DemoInput, request: Request):
    from .demo_workspaces import demo_identity
    return _response_with_session(request, *demo_identity(payload.persona))


class ClaimTrialInput(StrictInput):
    email: str = Field(min_length=3, max_length=254)
    password: str = Field(min_length=12, max_length=128)
    store_name: str = Field(min_length=1, max_length=100)


@router.post('/auth/claim-trial')
def save_trial(payload: ClaimTrialInput, request: Request):
    from .trial_onboarding import claim_trial
    context = require_context()
    name = payload.store_name.strip()
    if not name:
        raise HTTPException(422, '给门店起个名字吧')
    try:
        claim_trial(context, normalize_email(payload.email), payload.password, name)
    except IntegrityError:
        raise HTTPException(409, '这个邮箱已有账号，请使用原账号登录') from None
    return _response_with_session(request, context.user_id, context.tenant_id, context.store_id)


@router.post('/auth/login')
def login(payload: LoginInput, request: Request):
    email = normalize_email(payload.email)
    identity = token_hash(email + ':' + (request.client.host if request.client else 'unknown'))
    now = time.time()
    with db_connection() as connection:
        connection.execute(delete(login_attempts).where(login_attempts.c.created_at < now - 900))
        count = connection.execute(select(func.count()).select_from(login_attempts).where(
            login_attempts.c.identity_hash == identity, login_attempts.c.created_at > now - 900)).scalar_one()
        if count >= 12:
            raise HTTPException(429, '尝试次数较多，请稍后再登录')
        connection.execute(login_attempts.insert().values(id=uuid4().hex, identity_hash=identity, created_at=now))
        user = connection.execute(select(users).where(users.c.email == email, users.c.active.is_(True))).mappings().first()
    valid = verify_password(payload.password, user['password_hash'] if user else _DUMMY_HASH)
    if not valid or not user:
        raise HTTPException(401, '邮箱或密码不正确')
    rows = membership_rows(user['id'])
    if not rows:
        raise HTTPException(403, '账号目前没有可用的企业工作空间')
    with db_connection() as connection:
        connection.execute(delete(login_attempts).where(login_attempts.c.identity_hash == identity))
        old = request.cookies.get(COOKIE_NAME)
        if old:
            connection.execute(delete(sessions).where(sessions.c.token_hash == token_hash(old)))
        if passwords.check_needs_rehash(user['password_hash']):
            connection.execute(update(users).where(users.c.id == user['id']).values(password_hash=password_hash(payload.password)))
    chosen = rows[0]
    return _response_with_session(request, user['id'], chosen['tenant_id'], chosen['store_id'])


@router.get('/auth/me')
def me(request: Request):
    return me_payload(request)


@router.post('/auth/logout')
def logout(request: Request):
    token = request.cookies.get(COOKIE_NAME)
    if token:
        with db_connection() as connection:
            connection.execute(delete(sessions).where(sessions.c.token_hash == token_hash(token)))
    response = JSONResponse({'ok': True})
    response.delete_cookie(COOKIE_NAME, path='/')
    return response


@router.post('/auth/workspace')
def workspace(payload: WorkspaceInput, request: Request):
    context = require_context()
    rows = membership_rows(context.user_id, payload.tenant_id)
    if not rows:
        raise HTTPException(404, '没有找到这个工作空间')
    all_stores = any(item['store_id'] is None for item in rows)
    if payload.store_id:
        # Resolve in target tenant context before accepting a selected store.
        from .tenancy.context import set_context, reset_context, TenantContext
        grants = tuple((item['role'], item['store_id']) for item in rows)
        scoped = tuple(sorted({item['store_id'] for item in rows if item['store_id']}))
        target = TenantContext(context.user_id, payload.tenant_id, payload.store_id,
            tuple(item['role'] for item in rows if item['store_id'] in (None, payload.store_id)), scoped, all_stores,
            grants=grants, session_hash=context.session_hash)
        token = set_context(target)
        try:
            authorize_scope(payload.store_id)
            authorize_resource('store', payload.store_id)
        finally:
            reset_context(token)
    elif not all_stores:
        permitted = {item['store_id'] for item in rows}
        if len(permitted) == 1:
            payload.store_id = next(iter(permitted))
    with db_connection() as connection:
        connection.execute(update(sessions).where(sessions.c.token_hash == context.session_hash).values(
            tenant_id=payload.tenant_id, store_id=payload.store_id))
    return me_payload(request)


@router.get('/team/members')
def team_members():
    context = require_role('admin', 'operator', 'regional_manager', 'store_manager')
    with db_connection() as connection:
        rows = connection.execute(select(memberships, users.c.email, users.c.name).join(users,
            users.c.id == memberships.c.user_id).where(memberships.c.tenant_id == context.tenant_id)
            .order_by(memberships.c.created_at)).mappings().all()
    visible = [row for row in rows if context.all_stores or row['store_id'] in context.store_ids]
    return {'members': [{key: row[key] for key in ('id', 'user_id', 'email', 'name', 'role', 'store_id', 'active')} for row in visible]}


@router.post('/team/invitations', status_code=201)
def invite(payload: InviteInput):
    context = require_role('admin', 'operator')
    email = normalize_email(payload.email)
    if payload.role in {'admin', 'operator'} and payload.store_id:
        raise HTTPException(422, '企业管理员和总部运营应使用企业范围')
    if payload.role in {'admin', 'operator'} and 'admin' not in context.scoped_roles(None):
        raise HTTPException(403, '此角色需要企业管理员邀请')
    if payload.role in {'salesperson', 'store_manager', 'reviewer', 'regional_manager'} and not payload.store_id:
        raise HTTPException(422, '请选择成员负责的门店')
    if payload.store_id:
        authorize_resource('store', payload.store_id)
    token = secrets.token_urlsafe(40)
    identifier = uuid4().hex
    expires = time.time() + 48 * 60 * 60
    with db_connection() as connection:
        connection.execute(invitations.insert().values(id=identifier, token_hash=token_hash(token),
            tenant_id=context.tenant_id, email=email, role=payload.role, store_id=payload.store_id,
            created_by=context.user_id, expires_at=expires))
    audit('team.invite', 'invitation', identifier, {'role': payload.role, 'store_id': payload.store_id})
    return {'id': identifier, 'token': token, 'expires_at': expires, 'invite_url': '/join?token=' + token}


@router.get('/team/invitations')
def pending_invitations():
    context = require_role('admin', 'operator')
    with db_connection() as connection:
        rows = connection.execute(select(invitations.c.id, invitations.c.email, invitations.c.role,
            invitations.c.store_id, invitations.c.expires_at).where(invitations.c.tenant_id == context.tenant_id,
            invitations.c.used_at.is_(None), invitations.c.expires_at > time.time())).mappings().all()
    return {'invitations': [dict(row) for row in rows]}


@router.delete('/team/invitations/{identifier}')
def revoke_invitation(identifier: str):
    context = require_role('admin', 'operator')
    with db_connection() as connection:
        result = connection.execute(delete(invitations).where(invitations.c.id == identifier,
            invitations.c.tenant_id == context.tenant_id))
        if not result.rowcount:
            raise HTTPException(404, '没有找到这份邀请')
    audit('team.invite.revoke', 'invitation', identifier)
    return {'ok': True}


@router.post('/auth/accept-invitation')
def accept_invitation(payload: AcceptInput, request: Request):
    now = time.time()
    digest = token_hash(payload.token)
    new_hash = password_hash(payload.password)
    with db_connection() as connection:
        invitation = connection.execute(select(invitations).where(invitations.c.token_hash == digest,
            invitations.c.used_at.is_(None), invitations.c.expires_at > now)).mappings().first()
        if not invitation:
            raise HTTPException(410, '邀请已使用或已过期，请联系管理员重新邀请')
        # An invitation cannot grant a role after the inviter loses permission.
        inviter = connection.execute(select(memberships).where(memberships.c.user_id == invitation['created_by'],
            memberships.c.tenant_id == invitation['tenant_id'], memberships.c.active.is_(True),
            memberships.c.store_id.is_(None), memberships.c.role.in_(['admin', 'operator']))).mappings().all()
        if not inviter or (invitation['role'] in {'admin', 'operator'} and not any(r['role'] == 'admin' for r in inviter)):
            raise HTTPException(410, '这份邀请已失效，请联系企业管理员')
        consumed = connection.execute(update(invitations).where(invitations.c.id == invitation['id'],
            invitations.c.used_at.is_(None), invitations.c.expires_at > now).values(used_at=now))
        if consumed.rowcount != 1:
            raise HTTPException(410, '邀请已使用或已过期')
        user = connection.execute(select(users).where(users.c.email == invitation['email'])).mappings().first()
        if user:
            if not user['active'] or not verify_password(payload.password, user['password_hash']):
                raise HTTPException(401, '此邮箱已有账号，请使用原账号密码接受邀请')
            user_id = user['id']
        else:
            user_id = uuid4().hex
            connection.execute(users.insert().values(id=user_id, email=invitation['email'], name=payload.name.strip(),
                password_hash=new_hash, active=True, created_at=now))
        existing = connection.execute(select(memberships.c.id).where(memberships.c.user_id == user_id,
            memberships.c.tenant_id == invitation['tenant_id'], memberships.c.role == invitation['role'],
            memberships.c.store_id.is_(None) if invitation['store_id'] is None else memberships.c.store_id == invitation['store_id'])).first()
        if existing:
            connection.execute(update(memberships).where(memberships.c.id == existing.id).values(active=True))
        else:
            connection.execute(memberships.insert().values(id=uuid4().hex, user_id=user_id,
                tenant_id=invitation['tenant_id'], role=invitation['role'], store_id=invitation['store_id'], active=True, created_at=now))
    return _response_with_session(request, user_id, invitation['tenant_id'], invitation['store_id'])


@router.delete('/team/members/{identifier}')
def revoke_member(identifier: str):
    context = require_role('admin', 'operator')
    with db_connection() as connection:
        # Serialize changes to the company's final administrator invariant.
        if connection.dialect.name == 'postgresql':
            connection.execute(text('SELECT pg_advisory_xact_lock(hashtext(:scope))'),
                               {'scope': 'team:' + context.tenant_id})
        else:
            connection.execute(update(tenants).where(tenants.c.id == context.tenant_id).values(name=tenants.c.name))
        row = connection.execute(select(memberships).where(memberships.c.id == identifier,
            memberships.c.tenant_id == context.tenant_id, memberships.c.active.is_(True))).mappings().first()
        if not row:
            raise HTTPException(404, '没有找到这位成员')
        if row['role'] == 'admin':
            if 'admin' not in context.scoped_roles(None):
                raise HTTPException(403, '此操作需要企业管理员')
            others = connection.execute(select(func.count()).select_from(memberships).where(
                memberships.c.tenant_id == context.tenant_id, memberships.c.role == 'admin',
                memberships.c.active.is_(True), memberships.c.id != identifier)).scalar_one()
            if others == 0:
                raise HTTPException(409, '请至少保留一位企业管理员')
        connection.execute(update(memberships).where(memberships.c.id == identifier).values(active=False))
    audit('team.member.revoke', 'membership', identifier)
    return {'ok': True}


@router.get('/team/audit')
def audit_log():
    context = require_role('admin', 'operator')
    with db_connection() as connection:
        rows = connection.execute(select(audit_events).where(audit_events.c.tenant_id == context.tenant_id)
            .order_by(audit_events.c.created_at.desc()).limit(100)).mappings().all()
    return {'events': [{**dict(row), 'details': json.loads(row['details'])} for row in rows]}
