"""Versioned headquarters campaigns, store assignments and review/publication flow.

All private business objects use the central tenant resource registry. Cross-company
campaign offers have their own explicitly addressed, revocable grant table; no
recipient private content is exposed to the publisher.
"""
from __future__ import annotations

import hashlib
import json
import secrets
import time
import re
from datetime import datetime, timezone
from functools import wraps
from uuid import uuid4

from fastapi import HTTPException
from pydantic import BaseModel, ConfigDict, Field, field_validator
from sqlalchemy import Column, Float, String, Table, Text, or_, select, update

from .store_campaigns import CampaignInput, StoreRepository, plan_campaign
from .tenancy import (authorize_resource, authorize_scope, get_engine, list_resources,
                      load_body, require_context, save_body, transaction_context)
from .tenancy.context import MANAGERS
from .tenancy.database import db_connection, metadata
from .tenancy.resources import audit, service_update_body

CHANNEL_CAMPAIGN = 'channel_campaign'
ASSIGNMENT = 'channel_assignment'
SUBMISSION = 'channel_submission'
PUBLICATION = 'channel_publication'

channel_grants = Table('mq_channel_grants', metadata,
    Column('id', String(64), primary_key=True),
    Column('sender_tenant_id', String(64), nullable=False),
    Column('recipient_tenant_id', String(64), nullable=False),
    Column('snapshot', Text, nullable=False),
    Column('token_hash', String(64), nullable=False, unique=True),
    Column('expires_at', Float, nullable=False),
    Column('accepted_at', Float, nullable=True),
    Column('revoked_at', Float, nullable=True),
    Column('created_at', Float, nullable=False),
    extend_existing=True)


class ChannelModel(BaseModel):
    model_config = ConfigDict(extra='forbid')


class CampaignPackageInput(ChannelModel):
    title: str = Field(min_length=1, max_length=160)
    brief: str = Field(min_length=1, max_length=3000)
    product: str = Field(min_length=1, max_length=200)
    platform: str = Field(default='xiaohongshu', pattern='^(xiaohongshu|wechat|moments|poster)$')
    ecosystem_products: list[str] = Field(default_factory=list, max_length=4)
    store_ids: list[str] = Field(default_factory=list, max_length=100)
    goal: str = Field(default='引出具体咨询', max_length=300)
    required_copy: str = Field(default='', max_length=2000)
    editable_fields: list[str] = Field(default_factory=lambda: ['场景', '门店照片', '表达与联系入口'], max_length=20)
    starts_at: str | None = None
    ends_at: str | None = None

    @field_validator('title', 'brief', 'product')
    @classmethod
    def nonblank(cls, value):
        value = value.strip()
        if not value:
            raise ValueError('请填写活动名称、任务和准确产品')
        return value

    @field_validator('starts_at', 'ends_at')
    @classmethod
    def valid_time(cls, value):
        if not value:
            return None
        moment = datetime.fromisoformat(value.replace('Z', '+00:00'))
        if moment.tzinfo is None:
            raise ValueError('活动时间需要包含时区')
        return moment.astimezone(timezone.utc).isoformat()


class SubmissionInput(ChannelModel):
    assignment_id: str = Field(min_length=1, max_length=160)
    run_id: str = Field(min_length=1, max_length=160)
    note: str = Field(default='', max_length=2000)


class ReviewInput(ChannelModel):
    decision: str = Field(pattern='^(approved|changes_requested)$')
    note: str = Field(default='', max_length=2000)


class PublicationInput(ChannelModel):
    url: str = Field(default='', max_length=2048)
    note: str = Field(default='', max_length=2000)

    @field_validator('url')
    @classmethod
    def valid_url(cls, value):
        from urllib.parse import urlsplit
        if value:
            parts = urlsplit(value)
            if parts.scheme not in {'https', 'http'} or not parts.hostname or parts.username or parts.password:
                raise ValueError('请填写可访问的发布链接')
        return value


class GrantInput(ChannelModel):
    recipient_tenant_id: str = Field(min_length=1, max_length=64)
    days: int = Field(default=7, ge=1, le=90)


class AcceptGrantInput(ChannelModel):
    token: str = Field(min_length=20, max_length=200)
    store_ids: list[str] = Field(min_length=1, max_length=100)


def transactional(function):
    @wraps(function)
    def operation(*args, **kwargs):
        with transaction_context():
            return function(*args, **kwargs)
    return operation


def now_iso():
    return datetime.now(timezone.utc).isoformat()


def _roles(store_id=None):
    context = require_context()
    return set(context.scoped_roles(store_id))


def _hq():
    context = require_context()
    # Headquarters authority requires an enterprise-wide grant, not a store role.
    allowed = any(role in {'admin', 'operator'} and scope is None for role, scope in context.grants)
    if not context.is_legacy and not (allowed or (not context.grants and context.all_stores and _roles().intersection({'admin', 'operator'}))):
        raise HTTPException(403, '请由企业运营人员管理活动')
    return context


def _managed_store(store_id):
    authorize_scope(store_id, 'manage')
    return load_body('store', store_id)


def _load(kind, identifier):
    meta = authorize_resource(kind, identifier)
    if not meta.get('body'):
        raise HTTPException(404, '没有找到这份业务记录')
    return meta, dict(meta['body'])


def _transition(kind, meta, body):
    service_update_body(kind, meta['id'], body, expected_version=meta['version'])
    return body


def _store_ids(values):
    values = list(dict.fromkeys(values))
    for identifier in values:
        _managed_store(identifier)
    return values


def _active(package):
    if package['status'] != 'active':
        raise HTTPException(409, '这次活动尚未开始或已结束')
    now = datetime.now(timezone.utc)
    for key, check in [('starts_at', lambda value: now < value), ('ends_at', lambda value: now > value)]:
        if package.get(key) and check(datetime.fromisoformat(package[key])):
            raise HTTPException(409, '这次活动不在执行时间内')
    if package.get('source_grant_id'):
        _require_grant(package['source_grant_id'])


def _task(package, store, planned_task=None):
    plan = plan_campaign(CampaignInput(brief=package['brief'], product=package['product'],
        platform=package['platform'], store_ids=[store['id']], goal=package['goal'],
        ecosystem_products=package.get('ecosystem_products', [])), [store])
    task = dict(planned_task or plan['variants'][0]['task'])
    task['store_id'] = store['id']
    instructions = json.loads(task['requirements'])
    instructions['campaign'] = {'title': package['title'], 'version': package['version'],
        'required_copy': package.get('required_copy', ''), 'editable_fields': package.get('editable_fields', []),
        'starts_at': package.get('starts_at'), 'ends_at': package.get('ends_at')}
    task['requirements'] = json.dumps(instructions, ensure_ascii=False)
    return task


def _assign(package):
    # Plan the group together so adjacent stores do not all receive the same
    # narrative/visual skeleton, then persist a scoped task for each store.
    stores = [load_body('store', identifier) for identifier in package.get('store_ids', [])]
    group = plan_campaign(CampaignInput(brief=package['brief'], product=package['product'],
        platform=package['platform'], store_ids=[store['id'] for store in stores],
        goal=package['goal'], ecosystem_products=package.get('ecosystem_products', [])), stores)
    plans = {variant['store_id']: variant['task'] for variant in group['variants']}
    # Stable identifiers make repeated publish/accept idempotent.
    for store in stores:
        store_id = store['id']
        identifier = hashlib.sha256(f"{package['id']}:{package['version']}:{store_id}".encode()).hexdigest()[:32]
        try:
            _load(ASSIGNMENT, identifier)
            continue
        except HTTPException as exc:
            if exc.status_code != 404:
                raise
        timestamp = now_iso()
        planned_task = _task(package, store, plans[store_id])
        planned_task['assignment_id'] = identifier
        body = {'id': identifier, 'campaign_id': package['id'], 'campaign_version': package['version'],
            'title': package['title'], 'store_id': store_id, 'store_name': store['name'],
            'status': 'pending', 'task': planned_task, 'claimed_by': None,
            'submission_id': None, 'created_at': timestamp, 'updated_at': timestamp}
        save_body(ASSIGNMENT, identifier, body, store_id=store_id, visibility='store')


def list_campaigns():
    context = require_context()
    packages = [dict(row['body']) for row in list_resources(CHANNEL_CAMPAIGN)]
    if context.all_stores:
        return packages
    visible = []
    for package in packages:
        own_stores = [identifier for identifier in package.get('store_ids', []) if identifier in context.store_ids]
        if own_stores:
            package.update(store_ids=own_stores, assignments_count=len(own_stores))
            visible.append(package)
    return visible


@transactional
def create_campaign(payload: CampaignPackageInput):
    context = _hq()
    store_ids = _store_ids(payload.store_ids)
    if payload.starts_at and payload.ends_at and payload.starts_at >= payload.ends_at:
        raise HTTPException(422, '活动结束时间需要晚于开始时间')
    timestamp = now_iso()
    body = {**payload.model_dump(), 'store_ids': store_ids, 'id': uuid4().hex,
        'tenant_id': context.tenant_id, 'version': 1, 'status': 'draft',
        'created_by': context.user_id, 'created_at': timestamp, 'updated_at': timestamp,
        'assignments_count': len(store_ids)}
    save_body(CHANNEL_CAMPAIGN, body['id'], body, visibility='tenant')
    audit('campaign.created', CHANNEL_CAMPAIGN, body['id'])
    return body


@transactional
def update_campaign(identifier, payload: CampaignPackageInput):
    _hq()
    meta, old = _load(CHANNEL_CAMPAIGN, identifier)
    if old.get('source_grant_id'):
        raise HTTPException(403, '伙伴活动包的品牌内容由发布方维护')
    store_ids = _store_ids(payload.store_ids)
    if payload.starts_at and payload.ends_at and payload.starts_at >= payload.ends_at:
        raise HTTPException(422, '活动结束时间需要晚于开始时间')
    body = {**old, **payload.model_dump(), 'store_ids': store_ids, 'version': old['version'] + 1,
            'status': 'draft', 'updated_at': now_iso(), 'assignments_count': len(store_ids)}
    _transition(CHANNEL_CAMPAIGN, meta, body)
    _revoke_package_grants(identifier)
    audit('campaign.versioned', CHANNEL_CAMPAIGN, identifier, {'version': body['version']})
    return body


@transactional
def publish_campaign(identifier):
    _hq()
    meta, body = _load(CHANNEL_CAMPAIGN, identifier)
    if body['status'] == 'withdrawn':
        raise HTTPException(409, '已撤回的活动请创建新版本后发布')
    body.update(status='active', updated_at=now_iso())
    _transition(CHANNEL_CAMPAIGN, meta, body)
    _assign(body)
    audit('campaign.published', CHANNEL_CAMPAIGN, identifier, {'version': body['version']})
    return body


@transactional
def withdraw_campaign(identifier):
    _hq()
    meta, body = _load(CHANNEL_CAMPAIGN, identifier)
    body.update(status='withdrawn', updated_at=now_iso())
    _transition(CHANNEL_CAMPAIGN, meta, body)
    _revoke_package_grants(identifier)
    audit('campaign.withdrawn', CHANNEL_CAMPAIGN, identifier)
    return body


def list_assignments():
    rows = [dict(row['body']) for row in list_resources(ASSIGNMENT)]
    availability = {}
    for body in rows:
        key = (body['campaign_id'], body['campaign_version'])
        if key not in availability:
            try:
                _, package = _load(CHANNEL_CAMPAIGN, body['campaign_id'])
                _active(package)
                if package['version'] != body['campaign_version']:
                    raise HTTPException(409, '活动资料已更新')
                availability[key] = (True, '')
            except HTTPException as exc:
                availability[key] = (False, str(exc.detail))
        body['available'], body['unavailable_reason'] = availability[key]
    return rows


def _assignment(identifier):
    meta, body = _load(ASSIGNMENT, identifier)
    _, package = _load(CHANNEL_CAMPAIGN, body['campaign_id'])
    _active(package)
    if body['campaign_version'] != package['version']:
        raise HTTPException(409, '活动资料已更新，请使用最新任务')
    return meta, body, package


@transactional
def accept_assignment(identifier):
    context = require_context()
    meta, body, _ = _assignment(identifier)
    if body.get('claimed_by') and body['claimed_by'] != context.user_id and not _roles(body['store_id']).intersection(MANAGERS):
        raise HTTPException(409, '这份任务已有同事领取')
    if body['status'] in {'approved', 'published'}:
        return body
    body.update(status='creating', claimed_by=context.user_id, updated_at=now_iso())
    return _transition(ASSIGNMENT, meta, body)


def assignment_task(identifier):
    _, body, package = _assignment(identifier)
    return {'task': body['task'], 'assignment_id': identifier, 'campaign_version': package['version']}


def validate_generation_task(task):
    """Validate a previously issued assignment again at execution boundaries."""
    identifier = getattr(task, 'assignment_id', None) if not isinstance(task, dict) else task.get('assignment_id')
    if not identifier:
        return
    _, assignment, package = _assignment(identifier)
    value = task if isinstance(task, dict) else task.model_dump()
    if value.get('store_id') != assignment['store_id'] or value.get('product') != package['product'] or value.get('platform') != package['platform']:
        raise HTTPException(422, '创作需求与门店活动任务不一致')
    if value.get('ecosystem_products', []) != package.get('ecosystem_products', []):
        raise HTTPException(422, '请使用活动指定的生态产品组合')
    required = assignment['task']['requirements']
    actual = value.get('requirements') or ''
    if actual != required and (not actual.startswith(required + '\n') or len(actual) - len(required) > 5000):
        raise HTTPException(422, '活动资料已更新，请重新领取创作任务')


def _required_copy(package, record):
    required = package.get('required_copy', '').strip()
    if not required:
        return
    # Only published copy counts. Instructions, evidence and trace data may
    # repeat the required wording but do not mean it reached the actual draft.
    values = [record.get(key, '') for key in ('title', 'intro', 'cta')]
    values.extend(block.get('text', '') for block in record.get('blocks', []) if isinstance(block, dict))
    for card in record.get('creative_plan', {}).get('cards', []):
        if isinstance(card, dict):
            values.extend(card.get(key, '') for key in ('headline', 'body', 'caption', 'overlay_text'))
    text = ''.join(''.join(str(value).split()) for value in values)
    if ''.join(required.split()) not in text:
        raise HTTPException(422, '请保留活动必需文案后重新送审')


def _digest(record):
    return hashlib.sha256(json.dumps(record, sort_keys=True, ensure_ascii=False, separators=(',', ':')).encode()).hexdigest()


@transactional
def submit_content(payload: SubmissionInput):
    context = require_context()
    assignment_meta, assignment, package = _assignment(payload.assignment_id)
    if assignment.get('claimed_by') not in {None, context.user_id} and not _roles(assignment['store_id']).intersection(MANAGERS):
        raise HTTPException(403, '请由任务负责人提交内容')
    run_meta, record = _load('run', payload.run_id)
    if run_meta.get('store_id') != assignment['store_id']:
        raise HTTPException(422, '请提交这家门店创作的内容')
    if record.get('status') != 'ready':
        raise HTTPException(409, '请先完成内容生成和检查')
    _required_copy(package, record)
    # Compare the real generated task, not client-submitted product claims.
    generated_task = record.get('_poster_task', {})
    if generated_task.get('assignment_id') and generated_task['assignment_id'] != assignment['id']:
        raise HTTPException(422, '请提交这次活动任务的内容')
    if generated_task.get('product') != package['product'] or generated_task.get('platform') != package['platform']:
        raise HTTPException(422, '内容产品或发布平台与活动任务不一致')
    previous = None
    if assignment.get('submission_id'):
        _, previous = _load(SUBMISSION, assignment['submission_id'])
        if previous['run_id'] == payload.run_id and previous['content_hash'] == _digest(record) and previous['status'] in {'submitted', 'approved', 'published'}:
            return previous
    timestamp = now_iso()
    body = {'id': uuid4().hex, 'assignment_id': assignment['id'], 'campaign_id': package['id'],
        'campaign_version': package['version'], 'title': package['title'], 'store_id': assignment['store_id'],
        'store_name': assignment['store_name'], 'run_id': payload.run_id, 'revision': (previous['revision'] + 1) if previous else 1,
        'status': 'submitted', 'note': payload.note, 'review_note': '', 'submitted_by': context.user_id,
        'content_snapshot': record, 'content_hash': _digest(record), 'created_at': timestamp, 'updated_at': timestamp}
    save_body(SUBMISSION, body['id'], body, store_id=assignment['store_id'], visibility='store')
    assignment.update(status='submitted', claimed_by=assignment.get('claimed_by') or context.user_id,
                      submission_id=body['id'], updated_at=timestamp)
    _transition(ASSIGNMENT, assignment_meta, assignment)
    audit('content.submitted', SUBMISSION, body['id'], {'run_id': payload.run_id, 'revision': body['revision']})
    return body


def list_submissions():
    result = []
    for row in list_resources(SUBMISSION):
        body = dict(row['body'])
        body['image_previews'] = [
            {'asset_id': image['asset_id'], 'caption': image.get('caption', ''),
             'url': f"/api/channel/submissions/{body['id']}/assets/{image['asset_id']}"}
            for image in body.get('content_snapshot', {}).get('images', [])
            if isinstance(image, dict) and re.fullmatch(r'[A-Za-z0-9_-]{1,120}', image.get('asset_id', ''))]
        result.append(body)
    return result


def submission_asset(identifier, asset_id):
    """Read a selected snapshot image without sharing the author's media library."""
    if not re.fullmatch(r'[A-Za-z0-9_-]{1,120}', asset_id):
        raise HTTPException(404, '没有找到这张稿件图片')
    meta, submission = _load(SUBMISSION, identifier)
    snapshot = submission.get('content_snapshot', {})
    selected = {image.get('asset_id') for image in snapshot.get('images', []) if isinstance(image, dict)}
    if asset_id not in selected:
        raise HTTPException(404, '没有找到这张稿件图片')
    if not asset_id.startswith('upload-'):
        return None
    media_id = asset_id.removeprefix('upload-')
    from .tenancy.database import resources
    context = require_context()
    with db_connection() as connection:
        media = connection.execute(select(resources).where(resources.c.kind == 'media',
            resources.c.id == media_id, resources.c.tenant_id == context.tenant_id,
            resources.c.store_id == meta['store_id'])).mappings().first()
    if not media:
        raise HTTPException(404, '没有找到这张稿件图片')
    asset = next((asset for asset in snapshot.get('assets', []) if asset.get('id') == asset_id), None)
    expected = asset.get('sha256', '') if asset else ''
    if not re.fullmatch(r'[a-f0-9]{64}', expected):
        raise HTTPException(404, '这张稿件图片需要重新确认')
    from .media_routes import media_store
    # _read is an internal physical reader. The review projection above supplies
    # its authorization; no owner impersonation or visibility mutation occurs.
    record = media_store()._read(media_id)
    if not record or not record[1].startswith('image/'):
        raise HTTPException(404, '这张稿件图片已过期')
    data = record[0].read_bytes()
    if hashlib.sha256(data).hexdigest() != expected:
        raise HTTPException(409, '稿件图片已变化，请重新送审')
    return data, record[1]


def _submission(identifier):
    meta, body = _load(SUBMISSION, identifier)
    assignment_meta, assignment, package = _assignment(body['assignment_id'])
    if assignment.get('submission_id') != identifier or body['campaign_version'] != package['version']:
        raise HTTPException(409, '这份内容已有更新版本，请查看最新稿件')
    return meta, body, assignment_meta, assignment


@transactional
def review_submission(identifier, payload: ReviewInput):
    context = require_context()
    meta, body, assignment_meta, assignment = _submission(identifier)
    authorize_scope(body['store_id'], 'review')
    if body['status'] not in {'submitted', 'changes_requested'}:
        raise HTTPException(409, '这份内容当前无需审核')
    # Do not allow salesperson/reviewer to silently approve their own submission.
    if body['submitted_by'] == context.user_id and 'admin' not in _roles(body['store_id']):
        raise HTTPException(403, '请由另一位审核人员确认这份内容')
    timestamp = now_iso()
    body.update(status=payload.decision, review_note=payload.note, reviewed_by=context.user_id,
                reviewed_at=timestamp, updated_at=timestamp)
    _transition(SUBMISSION, meta, body)
    assignment.update(status=payload.decision, updated_at=timestamp)
    _transition(ASSIGNMENT, assignment_meta, assignment)
    audit('content.' + payload.decision, SUBMISSION, identifier, {'revision': body['revision']})
    return body


@transactional
def publish_submission(identifier, payload: PublicationInput):
    context = require_context()
    meta, body, assignment_meta, assignment = _submission(identifier)
    if body['submitted_by'] != context.user_id and not _roles(body['store_id']).intersection(MANAGERS):
        raise HTTPException(403, '请由提交人或店长确认发布')
    if body['status'] == 'published':
        return body
    if body['status'] != 'approved':
        raise HTTPException(409, '审核通过后再确认发布')
    _, current = _load('run', body['run_id'])
    if _digest(current) != body['content_hash']:
        raise HTTPException(409, '内容已修改，请重新送审')
    timestamp = now_iso()
    publication = {'id': uuid4().hex, 'submission_id': identifier, 'assignment_id': assignment['id'],
        'campaign_id': body['campaign_id'], 'campaign_version': body['campaign_version'],
        'store_id': body['store_id'], 'run_id': body['run_id'], 'revision': body['revision'],
        'url': payload.url, 'note': payload.note, 'source': 'manual',
        'confirmed_by': context.user_id, 'created_at': timestamp}
    save_body(PUBLICATION, publication['id'], publication, store_id=body['store_id'], visibility='store')
    body.update(status='published', publication=publication, updated_at=timestamp)
    _transition(SUBMISSION, meta, body)
    assignment.update(status='published', updated_at=timestamp)
    _transition(ASSIGNMENT, assignment_meta, assignment)
    audit('publication.manually_confirmed', PUBLICATION, publication['id'])
    return body


def campaign_progress():
    assignments = list_assignments()
    result = []
    for campaign in list_campaigns():
        rows = [row for row in assignments if row['campaign_id'] == campaign['id'] and row['campaign_version'] == campaign['version']]
        counts = {status: sum(row['status'] == status for row in rows) for status in
                  ('pending', 'creating', 'submitted', 'changes_requested', 'approved', 'published')}
        result.append({'campaign_id': campaign['id'], 'title': campaign['title'], 'version': campaign['version'],
                       'stores': len(rows), 'counts': counts})
    return result


def _revoke_package_grants(campaign_id):
    context = require_context()
    with db_connection() as connection:
        rows = connection.execute(select(channel_grants).where(
            channel_grants.c.sender_tenant_id == context.tenant_id,
            channel_grants.c.revoked_at.is_(None))).mappings().all()
        identifiers = [row['id'] for row in rows if json.loads(row['snapshot'])['id'] == campaign_id]
        if identifiers:
            connection.execute(update(channel_grants).where(channel_grants.c.id.in_(identifiers),
                channel_grants.c.sender_tenant_id == context.tenant_id).values(revoked_at=time.time()))


def _grant_rows():
    context = require_context()
    return or_(channel_grants.c.sender_tenant_id == context.tenant_id,
               channel_grants.c.recipient_tenant_id == context.tenant_id)


def _grant_public(row):
    body = {key: value for key, value in dict(row).items() if key not in {'token_hash', 'snapshot'}}
    snapshot = json.loads(row['snapshot'])
    body.update(title=snapshot['title'], campaign_id=snapshot['id'], campaign_version=snapshot['version'])
    return body


def list_grants():
    with db_connection() as connection:
        rows = connection.execute(select(channel_grants).where(_grant_rows()).order_by(channel_grants.c.created_at.desc())).mappings().all()
    return [_grant_public(row) for row in rows]


@transactional
def create_grant(identifier, payload: GrantInput):
    context = _hq()
    _, package = _load(CHANNEL_CAMPAIGN, identifier)
    _active(package)
    if payload.recipient_tenant_id == context.tenant_id:
        raise HTTPException(422, '企业内门店直接通过活动分配')
    if package.get('source_grant_id'):
        raise HTTPException(403, '合作活动不能再次转授权')
    token = secrets.token_urlsafe(32)
    timestamp = time.time()
    # Only the explicitly shared immutable campaign package is included, not any
    # source store profiles, customer input, drafts, recordings or private assets.
    shared_fields = {'id', 'title', 'brief', 'product', 'platform', 'ecosystem_products',
                     'goal', 'required_copy', 'editable_fields', 'starts_at', 'ends_at', 'version', 'status'}
    snapshot = {key: value for key, value in package.items() if key in shared_fields}
    row = {'id': uuid4().hex, 'sender_tenant_id': context.tenant_id,
           'recipient_tenant_id': payload.recipient_tenant_id, 'snapshot': json.dumps(snapshot, ensure_ascii=False),
           'token_hash': hashlib.sha256(token.encode()).hexdigest(), 'expires_at': timestamp + payload.days * 86400,
           'accepted_at': None, 'revoked_at': None, 'created_at': timestamp}
    with db_connection() as connection:
        connection.execute(channel_grants.insert().values(**row))
    audit('campaign.granted', CHANNEL_CAMPAIGN, identifier, {'grant_id': row['id'], 'recipient': payload.recipient_tenant_id})
    return {**_grant_public(row), 'token': token}


def _require_grant(identifier):
    context = require_context()
    with db_connection() as connection:
        row = connection.execute(select(channel_grants).where(channel_grants.c.id == identifier,
                   channel_grants.c.recipient_tenant_id == context.tenant_id)).mappings().first()
    if not row or row['revoked_at'] or row['expires_at'] <= time.time():
        raise HTTPException(409, '活动共享已结束，请联系活动发布方')
    return row


@transactional
def accept_grant(payload: AcceptGrantInput):
    context = _hq()
    stores = _store_ids(payload.store_ids)
    digest = hashlib.sha256(payload.token.encode()).hexdigest()
    with db_connection() as connection:
        row = connection.execute(select(channel_grants).where(channel_grants.c.token_hash == digest,
            channel_grants.c.recipient_tenant_id == context.tenant_id).with_for_update()).mappings().first()
    if not row or row['revoked_at'] or row['expires_at'] <= time.time():
        raise HTTPException(404, '没有找到可接收的活动邀请')
    # The claim is one-time; stable imported campaign ID permits safe retries.
    identifier = 'partner-' + row['id']
    if row['accepted_at']:
        return load_body(CHANNEL_CAMPAIGN, identifier)
    package = json.loads(row['snapshot'])
    timestamp = now_iso()
    body = {**package, 'id': identifier, 'tenant_id': context.tenant_id,
        'source_grant_id': row['id'], 'source_tenant_id': row['sender_tenant_id'],
        'store_ids': stores, 'assignments_count': len(stores), 'status': 'active',
        'created_by': context.user_id, 'created_at': timestamp, 'updated_at': timestamp}
    save_body(CHANNEL_CAMPAIGN, identifier, body, visibility='tenant')
    _assign(body)
    with db_connection() as connection:
        updated = connection.execute(update(channel_grants).where(channel_grants.c.id == row['id'],
            channel_grants.c.recipient_tenant_id == context.tenant_id, channel_grants.c.accepted_at.is_(None),
            channel_grants.c.revoked_at.is_(None)).values(accepted_at=time.time()))
        if updated.rowcount != 1:
            raise HTTPException(409, '邀请状态已变化，请刷新活动列表')
    audit('campaign.grant_accepted', CHANNEL_CAMPAIGN, identifier, {'grant_id': row['id']})
    return body


@transactional
def revoke_grant(identifier):
    context = _hq()
    with db_connection() as connection:
        row = connection.execute(select(channel_grants).where(channel_grants.c.id == identifier,
                channel_grants.c.sender_tenant_id == context.tenant_id)).mappings().first()
        if not row:
            raise HTTPException(404, '没有找到这份活动授权')
        connection.execute(update(channel_grants).where(channel_grants.c.id == identifier,
            channel_grants.c.sender_tenant_id == context.tenant_id).values(revoked_at=time.time()))
    audit('campaign.grant_revoked', CHANNEL_CAMPAIGN, identifier)
    return {**_grant_public(row), 'revoked_at': time.time()}
