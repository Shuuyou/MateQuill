"""One authorization gate for every business object and its derived outputs."""
import json
import time
from uuid import uuid4
from fastapi import HTTPException
from sqlalchemy import select, update, cast, func
from sqlalchemy.exc import IntegrityError
from .context import require_context, authorize_scope, MANAGERS
from .database import resources, audit_events, db_connection


def _decode(row):
    result = dict(row)
    result['body'] = json.loads(result['body']) if result.get('body') is not None else None
    return result


def _permitted(context, row, action):
    if context.is_legacy:
        return True
    if row['tenant_id'] != context.tenant_id:
        return False
    if action in {'read', 'view', 'download', 'export', 'list'} and row['visibility'] == 'tenant':
        # Sharing does not move the original store or grant editing/review.
        return True
    store = row.get('store_id')
    if store is not None and not context.all_stores and store not in context.store_ids:
        return False
    roles = set(context.scoped_roles(store))
    owner = row['owner_id'] == context.user_id
    manager = bool(roles.intersection(MANAGERS))
    if action in {'manage', 'delete'}:
        return manager or owner
    if action in {'review', 'approve'}:
        return manager or 'reviewer' in roles
    if action in {'edit', 'write', 'update'}:
        return owner or manager
    if row['visibility'] == 'private':
        return owner or manager
    if row['visibility'] == 'tenant':
        return True
    return store is not None and bool(roles)


def authorize_resource(kind, identifier, action='read'):
    context = require_context()
    if context.is_legacy:
        return {'kind': kind, 'id': identifier, 'tenant_id': context.tenant_id,
                'store_id': context.store_id, 'owner_id': context.user_id,
                'visibility': 'private', 'body': None, 'version': 1}
    with db_connection() as connection:
        row = connection.execute(select(resources).where(resources.c.kind == kind,
                                  resources.c.id == identifier,
                                  resources.c.tenant_id == context.tenant_id)).mappings().first()
    if not row or not _permitted(context, row, action):
        raise HTTPException(404, '没有找到这份内容')
    return _decode(row)


def register_resource(kind, identifier, store_id=None, visibility='private', body=None):
    context = require_context()
    if context.is_legacy:
        return {'kind': kind, 'id': identifier, 'tenant_id': context.tenant_id,
                'store_id': store_id, 'owner_id': context.user_id, 'visibility': visibility, 'body': body}
    if visibility not in {'private', 'store', 'tenant'}:
        raise ValueError('Invalid resource visibility')
    store_id = store_id if store_id is not None else (None if visibility == 'tenant' else context.store_id)
    authorize_scope(store_id)
    if visibility == 'tenant' and not set(context.scoped_roles(None)).intersection({'admin', 'operator'}):
        raise HTTPException(403, '企业共享需要管理员确认')
    now = time.time()
    values = dict(kind=kind, id=identifier, tenant_id=context.tenant_id, store_id=store_id,
                  owner_id=context.user_id, visibility=visibility,
                  body=json.dumps(body, ensure_ascii=False) if body is not None else None,
                  version=1, created_at=now, updated_at=now)
    try:
        with db_connection() as connection:
            found = connection.execute(select(resources).where(resources.c.kind == kind,
                                       resources.c.id == identifier)).mappings().first()
            if found:
                if not _permitted(context, found, 'edit'):
                    raise HTTPException(404, '没有找到这份内容')
                return _decode(found)
            connection.execute(resources.insert().values(**values))
    except IntegrityError:
        # An existing ID hidden by RLS must not be overwritten or disclosed.
        raise HTTPException(404, '没有找到这份内容') from None
    return {**values, 'body': body}


def save_body(kind, identifier, body, store_id=None, visibility='private'):
    context = require_context()
    if context.is_legacy:
        return register_resource(kind, identifier, store_id, visibility, body)
    try:
        old = authorize_resource(kind, identifier, 'edit')
    except HTTPException as exc:
        if exc.status_code != 404:
            raise
        return register_resource(kind, identifier, store_id, visibility, body)
    if store_id is not None and old['store_id'] != store_id:
        raise HTTPException(409, '内容所属门店不能直接更改')
    with db_connection() as connection:
        connection.execute(update(resources).where(resources.c.kind == kind, resources.c.id == identifier,
                           resources.c.tenant_id == context.tenant_id).values(
                           body=json.dumps(body, ensure_ascii=False), updated_at=time.time(),
                           version=resources.c.version + 1))
    return authorize_resource(kind, identifier)


def load_body(kind, identifier, action='read'):
    return authorize_resource(kind, identifier, action).get('body')


def service_update_body(kind, identifier, body, *, expected_version=None):
    """Persist a route-authorized workflow transition, not a public edit API.

    The caller MUST verify its workflow role, assignment and transition first.
    Tenant/read scope and optimistic concurrency are always enforced here.
    Ownership and visibility cannot be changed through this operation.
    """
    context = require_context()
    old = authorize_resource(kind, identifier)
    query = update(resources).where(resources.c.kind == kind, resources.c.id == identifier,
                                    resources.c.tenant_id == context.tenant_id)
    if expected_version is not None:
        query = query.where(resources.c.version == expected_version)
    with db_connection() as connection:
        result = connection.execute(query.values(body=json.dumps(body, ensure_ascii=False),
                    updated_at=time.time(), version=resources.c.version + 1))
        if result.rowcount != 1:
            raise HTTPException(409, '内容刚刚已更新，请刷新后重试')
    return authorize_resource(kind, identifier)


def list_resources(kind, action='read'):
    context = require_context()
    if context.is_legacy:
        return []
    with db_connection() as connection:
        rows = connection.execute(select(resources).where(resources.c.kind == kind,
                                  resources.c.tenant_id == context.tenant_id)
                                  .order_by(resources.c.created_at.desc())).mappings().all()
    return [_decode(row) for row in rows if _permitted(context, row, action)]


def list_resource_headers(kind, limit=200):
    """History never transfers embedded poster rasters into the API process."""
    context = require_context()
    if context.is_legacy:
        return []
    with db_connection() as connection:
        if connection.dialect.name == 'postgresql':
            from sqlalchemy.dialects.postgresql import JSONB
            document = cast(resources.c.body, JSONB)
            title, state = document['title'].astext, document['status'].astext
        else:
            title, state = func.json_extract(resources.c.body, '$.title'), func.json_extract(resources.c.body, '$.status')
        columns = [column for column in resources.c if column.name != 'body']
        query = select(*columns, title.label('title'), state.label('status')).where(
            resources.c.kind == kind, resources.c.tenant_id == context.tenant_id)
        query = query.order_by(resources.c.created_at.desc(), resources.c.id.desc())
        result = []
        for row in connection.execution_options(stream_results=True).execute(query).mappings():
            if _permitted(context, row, 'read'):
                result.append(dict(row))
                if len(result) >= min(max(limit, 1), 200):
                    break
        return result


def set_resource_visibility(kind, identifier, visibility):
    if visibility not in {'private', 'store', 'tenant'}:
        raise ValueError('Invalid visibility')
    context = require_context()
    old = authorize_resource(kind, identifier, 'edit')
    if visibility == 'tenant' and not set(context.scoped_roles(None)).intersection({'admin', 'operator'}):
        raise HTTPException(403, '企业共享需要管理员确认')
    if visibility == 'store' and not old.get('store_id'):
        raise HTTPException(422, '请选择内容所属门店')
    with db_connection() as connection:
        connection.execute(update(resources).where(resources.c.kind == kind, resources.c.id == identifier,
                           resources.c.tenant_id == context.tenant_id).values(visibility=visibility, updated_at=time.time()))
    return authorize_resource(kind, identifier)


def audit(action, resource_kind='', resource_id='', details=None):
    context = require_context()
    if context.is_legacy:
        return
    with db_connection() as connection:
        connection.execute(audit_events.insert().values(id=uuid4().hex, tenant_id=context.tenant_id,
                           actor_id=context.user_id, action=action, resource_kind=resource_kind,
                           resource_id=resource_id, details=json.dumps(details or {}, ensure_ascii=False),
                           created_at=time.time()))
