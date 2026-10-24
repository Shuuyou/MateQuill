"""Tenant-scoped conversation state and versioned artifact references."""
import hashlib
import json
import time
from uuid import uuid4

from fastapi import HTTPException
from sqlalchemy import select, update

from ..tenancy import (require_context, register_resource, authorize_resource,
    list_resources, service_update_body, transaction_context)
from ..tenancy.database import db_connection, resources

CONVERSATION = 'agent_conversation'
TURN = 'agent_turn'
MEMORY = 'agent_memory'


def live_context():
    from ..resource_access import ensure_live_context
    context = ensure_live_context() or require_context()
    if not context.store_id:
        raise HTTPException(409, '选择一家门店后，就可以接着聊')
    return context


def conversation_row(identifier, *, editing=False):
    context = live_context()
    row = authorize_resource(CONVERSATION, identifier)
    body = row['body'] or {}
    # Private assistant conversations stay personal, including for managers.
    if body.get('audience') != 'group' and row['owner_id'] != context.user_id:
        raise HTTPException(404, '没有找到这段对话')
    if editing and row['store_id'] != context.store_id:
        raise HTTPException(409, '请先回到这家门店继续对话')
    return row


def artifact_reference(kind, identifier, *, audience='private'):
    if kind not in {'run','poster','design'}:
        raise HTTPException(422, '作品类型无效')
    row = authorize_resource(kind, identifier)
    context = require_context()
    if row['store_id'] != context.store_id or (row['visibility'] == 'private' and row['owner_id'] != context.user_id):
        raise HTTPException(404, '没有找到当前门店可以继续的作品')
    if audience == 'group' and row['visibility'] == 'private':
        raise HTTPException(404, '这份作品可以在私聊中继续编辑')
    body = row['body'] or {}
    if kind == 'run' and body.get('status') != 'ready':
        raise HTTPException(409, '这份作品还没有完成')
    if kind == 'design':
        from ..design_service import load_design
        load_design(identifier)
    return {'kind': kind, 'id': identifier,
        'version': body.get('version', row['version']) if kind in {'poster','design'} else row['version'],
        'title': body.get('title') or ('图片设计' if kind in {'poster','design'} else '门店创作'),
        'url': f'/api/runs/{identifier}' if kind == 'run' else f'/api/designs/{identifier}' if kind == 'design' else f'/api/posters/{identifier}/preview.svg'}


def project_conversation(row):
    body = {**row['body'], 'version': row['version']}
    if body.get('current_opportunity'):
        from .marketing_opportunity import get_opportunity
        try:
            body['current_opportunity'] = get_opportunity(body['current_opportunity']['id'], audience=body['audience'])
        except HTTPException:
            body['current_opportunity'] = None
    body.setdefault('pending_questions', [])
    body.setdefault('last_turn_id', None)
    if body.get('active_turn_id'):
        from .jobs import dispatch
        with db_connection() as connection:
            state = connection.execute(select(dispatch.c.status).where(
                dispatch.c.id == body['active_turn_id'])).scalar_one_or_none()
        if state not in {'queued','running'}:
            body['active_turn_id'] = None
    reference = body.get('current_artifact')
    if reference:
        try:
            body['current_artifact'] = artifact_reference(reference['kind'], reference['id'], audience=body['audience'])
        except HTTPException:
            body['current_artifact'] = None
    return body


def create_conversation(title='新的创作', *, audience='private'):
    context = live_context()
    if audience not in {'private', 'group'}:
        raise ValueError('Unknown conversation audience')
    identifier = uuid4().hex
    body = {'id': identifier, 'title': title.strip()[:100] or '新的创作', 'audience': audience,
        'messages': [], 'current_artifact': None, 'active_turn_id': None, 'updated_at': time.time()}
    row = register_resource(CONVERSATION, identifier, body=body,
        visibility='store' if audience == 'group' else 'private')
    return project_conversation(row)


def get_conversation(identifier):
    return project_conversation(conversation_row(identifier))


def conversations():
    context = live_context()
    return {'items': [project_conversation(row) for row in list_resources(CONVERSATION)
        if row['store_id'] == context.store_id and
        (row['owner_id'] == context.user_id or (row.get('body') or {}).get('audience') == 'group')][:100]}


def link_artifact(identifier, kind, artifact_id, expected_version=None, *, artifact_version=None):
    row = conversation_row(identifier, editing=True)
    if project_conversation(row).get('active_turn_id'):
        raise HTTPException(409, '这次创作还在继续，完成后再连接作品')
    if expected_version is not None and row['version'] != expected_version:
        raise HTTPException(409, '对话刚刚有更新，请刷新后继续')
    reference = artifact_reference(kind, artifact_id, audience=row['body']['audience'])
    if artifact_version is not None and reference['version'] != artifact_version:
        raise HTTPException(409, '这份作品刚刚有更新，请先查看最新版本')
    body = {**row['body'], 'current_artifact': reference, 'updated_at': time.time()}
    return project_conversation(service_update_body(CONVERSATION, identifier, body, expected_version=row['version']))


def get_turn(conversation_id, identifier):
    conversation_row(conversation_id)
    row = authorize_resource(TURN, identifier)
    if row['body'].get('conversation_id') != conversation_id:
        raise HTTPException(404, '没有找到这次创作')
    body = row['body']
    projected = {key: body.get(key) for key in ('id', 'status', 'conversation_id', 'message',
          'artifact', 'quotation', 'questions', 'created_at', 'updated_at', 'error')}
    from .jobs import dispatch
    with db_connection() as connection:
        state = connection.execute(select(dispatch.c.status).where(dispatch.c.id == identifier)).scalar_one_or_none()
    if state in {'queued','running'}:
        projected['status'] = state
    elif state == 'cancelled':
        projected.update(status='failed',message='这次任务的权限已经更新，可以开始新的创作。')
    return projected


def turn_id(conversation_id, client_turn_id):
    context = require_context()
    value = ':'.join((context.tenant_id, context.user_id, conversation_id, client_turn_id))
    return hashlib.sha256(value.encode()).hexdigest()[:40]


def update_turn(identifier, mutate):
    row = authorize_resource(TURN, identifier)
    body = mutate(dict(row['body']))
    return service_update_body(TURN, identifier, body, expected_version=row['version'])
