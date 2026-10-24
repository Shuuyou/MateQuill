"""Durable generation dispatch with revocable identity and tenant budgets.

Dispatch contains control-plane locators only; task and result bodies stay in
the authorized resource store. A compare-and-swap lease permits multiple workers
without executing the same queued item concurrently. Expired leases are retried.
"""
import asyncio
from contextlib import contextmanager
from contextvars import ContextVar
from datetime import datetime, timezone
import json
import os
import time
from uuid import uuid4
from zoneinfo import ZoneInfo

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field, ValidationError
from sqlalchemy import Column, Float, String, Table, Text, select, update, func
from sqlalchemy.exc import IntegrityError
from starlette.requests import Request

from .models import TaskInput
from .tenancy import (TenantContext, require_context, get_context, refresh_context,
    set_context, reset_context, save_body, load_body, audit)
from .tenancy.database import metadata, db_connection

dispatch = Table('mq_job_dispatch', metadata,
    Column('id', String(64), primary_key=True), Column('tenant_id', String(64), nullable=False),
    Column('user_id', String(64), nullable=False), Column('store_id', String(120)),
    Column('session_hash', String(64), nullable=False), Column('status', String(24), nullable=False),
    Column('claim_token', String(64), nullable=True),
    Column('lease_until', Float, nullable=False, default=0), Column('created_at', Float, nullable=False),
    Column('idempotency_key', String(200), unique=True, nullable=False))
usage = Table('mq_usage_ledger', metadata,
    Column('id', String(64), primary_key=True), Column('tenant_id', String(64), nullable=False),
    Column('user_id', String(64), nullable=False), Column('store_id', String(120)),
    Column('day', String(10), nullable=False), Column('status', String(24), nullable=False),
    Column('created_at', Float, nullable=False), Column('finished_at', Float))
router = APIRouter(prefix='/api', tags=['generation-jobs-and-usage'])
_workers = set()
_stop = None
_active_job = ContextVar('matequill_active_job', default=None)
_active_claim = ContextVar('matequill_active_claim', default=None)
_active_reservation = ContextVar('matequill_active_reservation', default=None)


def _budget_scope(context):
    from .tenancy.auth import guest_state
    guest = not context.is_legacy and guest_state(context.user_id, context.tenant_id)['is_guest']
    return guest, int(os.getenv('MATEQUILL_TRIAL_CREATIONS' if guest else 'MATEQUILL_DAILY_CREATIONS', '3' if guest else '100'))


def _day():
    return datetime.now(ZoneInfo(os.getenv('MATEQUILL_BUSINESS_TIMEZONE', 'Asia/Shanghai'))).date().isoformat()


@contextmanager
def generation_budget():
    context = get_context()
    if context is None or context.is_legacy:
        yield
        return
    identifier = ((_active_job.get() + ':') if _active_job.get() else '') + uuid4().hex[:24]
    guest, limit = _budget_scope(context)
    with db_connection() as connection:
        if connection.dialect.name == 'postgresql':
            from sqlalchemy import text
            connection.execute(text('SELECT pg_advisory_xact_lock(hashtext(:tenant))'),
                               {'tenant': context.tenant_id})
        else:
            # A write obtains SQLite's serialization lock before the count.
            connection.execute(update(usage).where(usage.c.id == '__budget_lock__').values(status='reserved'))
        connection.execute(update(usage).where(usage.c.tenant_id == context.tenant_id,
            usage.c.status == 'reserved', usage.c.created_at < time.time() - 600).values(status='failed', finished_at=time.time()))
        conditions = [usage.c.tenant_id == context.tenant_id, usage.c.status.in_(['reserved', 'completed'])]
        if not guest:
            conditions.append(usage.c.day == _day())
        count = connection.execute(select(func.count()).select_from(usage).where(*conditions)).scalar_one()
        if count >= limit:
            raise HTTPException(429, '保存到你的门店后，可以继续创作' if guest else '今日创作额度已用完，请联系企业管理员')
        connection.execute(usage.insert().values(id=identifier, tenant_id=context.tenant_id,
            user_id=context.user_id, store_id=context.store_id, day=_day(), status='reserved', created_at=time.time()))
    status = 'failed'
    reservation = _active_reservation.set(identifier)
    try:
        yield
        status = 'completed'
    finally:
        try:
            with db_connection() as connection:
                connection.execute(update(usage).where(usage.c.id == identifier,
                    usage.c.tenant_id == context.tenant_id, usage.c.status == 'reserved')
                    .values(status=status, finished_at=time.time()))
        finally:
            _active_reservation.reset(reservation)


def persist_generation_result(identifier, record):
    """Commit the run, recoverable job result and one usage unit atomically."""
    from .run_records import save_run
    from .tenancy.database import transaction_context
    context = get_context()
    if context is None or context.is_legacy:
        save_run(identifier, record)
        return
    claim = _active_claim.get()
    with transaction_context() as connection:
        if claim:
            current = connection.execute(select(dispatch).where(dispatch.c.id == claim[0],
                dispatch.c.tenant_id == context.tenant_id).with_for_update()).mappings().first()
            if not current or current['claim_token'] != claim[1] or current['lease_until'] <= time.time() or current['status'] != 'running':
                raise HTTPException(409, '任务正在由新的工作进程接续')
        save_run(identifier, record)
        from .gtm_agent.effects import checkpoint_generated_artifact
        checkpoint_generated_artifact('run', identifier, record)
        if claim:
            job = load_body('generation_job', claim[0], action='write')
            job['committed_result'] = record
            save_body('generation_job', claim[0], job)
        reserved = _active_reservation.get()
        if reserved:
            connection.execute(update(usage).where(usage.c.id == reserved,
                usage.c.tenant_id == context.tenant_id, usage.c.status == 'reserved')
                .values(status='failed' if record.get('status') in {'blocked', 'error', 'needs_input'} else 'completed',
                        finished_at=time.time()))


class JobInput(BaseModel):
    task: TaskInput
    idempotency_key: str = Field(default_factory=lambda: uuid4().hex, min_length=8, max_length=100)


@router.post('/jobs', status_code=202)
def enqueue(payload: JobInput):
    return enqueue_operation(payload)


def enqueue_operation(payload: JobInput, *, operation='content', parameters=None):
    """Share the existing fenced queue with registered creation operations."""
    if operation not in {'content', 'design', 'poster'}:
        raise ValueError('Unknown generation operation')
    from .resource_access import validate_task_resources
    context = require_context()
    validate_task_resources(payload.task)
    if not context.store_id:
        raise HTTPException(422, '请先选择本次创作的门店')
    identifier = uuid4().hex
    # Idempotency never exposes the jobs of another enterprise or user.
    key = context.tenant_id + ':' + context.user_id + ':' + payload.idempotency_key
    body = {'id': identifier, 'status': 'queued', 'task': payload.task.model_dump(mode='json'),
            'created_at': time.time()}
    if operation != 'content':
        body.update(operation=operation, parameters=parameters or {})
    from .tenancy.database import transaction_context
    try:
        with transaction_context() as connection:
            if connection.dialect.name == 'postgresql':
                from sqlalchemy import text
                connection.execute(text('SELECT pg_advisory_xact_lock(hashtext(:tenant))'), {'tenant': context.tenant_id})
            else:
                connection.execute(update(dispatch).where(dispatch.c.id == '__queue_lock__').values(status='queued'))
            previous = connection.execute(select(dispatch).where(dispatch.c.idempotency_key == key)).mappings().first()
            if previous:
                prior_body = load_body('generation_job', previous['id'])
                if (prior_body.get('task') != payload.task.model_dump(mode='json')
                        or prior_body.get('operation', 'content') != operation
                        or prior_body.get('parameters', {}) != (parameters or {})):
                    raise HTTPException(409, '这次请求的需求已变化，请重新开始创作')
                return {'id': previous['id'], 'status': previous['status']}
            count = connection.execute(select(func.count()).select_from(dispatch).where(
                dispatch.c.tenant_id == context.tenant_id, dispatch.c.status.in_(['queued', 'running']))).scalar_one()
            if count >= 20:
                raise HTTPException(429, '本企业正在处理的创作较多，请稍后再试')
            save_body('generation_job', identifier, body)
            connection.execute(dispatch.insert().values(id=identifier, tenant_id=context.tenant_id,
                user_id=context.user_id, store_id=context.store_id, session_hash=context.session_hash,
                status='queued', lease_until=0, created_at=time.time(), idempotency_key=key))
            audit('job.queued', 'generation_job', identifier)
    except IntegrityError:
        with db_connection() as connection:
            previous = connection.execute(select(dispatch).where(dispatch.c.idempotency_key == key)).mappings().one()
        return {'id': previous['id'], 'status': previous['status']}
    return {'id': identifier, 'status': 'queued'}


@router.get('/jobs/{identifier}')
def job_result(identifier: str):
    body = load_body('generation_job', identifier)
    context = require_context()
    with db_connection() as connection:
        state = connection.execute(select(dispatch.c.status).where(dispatch.c.id == identifier,
            dispatch.c.tenant_id == context.tenant_id)).scalar_one_or_none()
    if state:
        body = {**body, 'status': state}
    if state != 'succeeded':
        body.pop('result', None)
    if state in {'cancelled', 'failed'} and not body.get('error'):
        body['error'] = {'message': '任务权限已更新，请重新开始创作' if state == 'cancelled' else '这次创作未完成，请重试'}
    # Never return the source prompt/task through the polling contract.
    return {key: value for key, value in body.items() if key not in {'task', 'committed_result', 'parameters'}}


@router.post('/jobs/{identifier}/cancel', status_code=202)
def cancel_job(identifier: str):
    """Cooperative cancel: queued jobs stop immediately, running jobs are
    flagged and the worker's heartbeat cancels the in-flight operation."""
    from .tenancy import authorize_resource
    from .tenancy.database import transaction_context
    context = require_context()
    resource = authorize_resource('generation_job', identifier, 'edit')
    if resource['owner_id'] != context.user_id:
        raise HTTPException(403, '只有发起这次创作的账号可以停止')
    with transaction_context() as connection:
        row = connection.execute(select(dispatch).where(dispatch.c.id == identifier,
            dispatch.c.tenant_id == context.tenant_id).with_for_update()).mappings().first()
        if not row:
            raise HTTPException(404, '这次创作不存在')
        if row['status'] in {'succeeded', 'failed', 'cancelled'}:
            raise HTTPException(409, '这次创作已经结束，无需停止')
        if row['status'] == 'queued':
            changed = connection.execute(update(dispatch).where(dispatch.c.id == identifier,
                dispatch.c.status == 'queued').values(status='cancelled', lease_until=0))
            if changed.rowcount != 1:
                raise HTTPException(409, '任务状态刚刚更新，请刷新查看')
        body = load_body('generation_job', identifier, action='write')
        if row['status'] == 'queued':
            body.update(status='cancelled', error={'message': '已按你的要求停止这次创作'},
                        finished_at=time.time())
        else:
            body['cancel_requested'] = True
        save_body('generation_job', identifier, body)
        audit('job.cancel_requested' if row['status'] == 'running' else 'job.cancelled',
              'generation_job', identifier)
    return {'id': identifier, 'status': 'cancel_requested' if row['status'] == 'running' else 'cancelled'}


@router.post('/jobs/{identifier}/retry', status_code=202)
def retry_design_job(identifier: str):
    """Resume a saved design through the same fenced queue, never repurchase an unknown request."""
    from .tenancy import authorize_resource
    from .tenancy.database import transaction_context
    from .resource_access import ensure_live_context, validate_task_resources
    from .design_service import source_record
    import base64
    import hashlib
    context = ensure_live_context() or require_context()
    if context.is_legacy or not context.store_id:
        raise HTTPException(403, '登录原门店后继续这次设计')
    try:
        resource = authorize_resource('generation_job', identifier, 'edit')
    except HTTPException as exc:
        if exc.status_code == 404:
            raise HTTPException(403, '无法继续这次设计') from None
        raise
    if resource['owner_id'] != context.user_id or resource['store_id'] != context.store_id:
        raise HTTPException(403, '回到创建这次设计的账号和门店后继续')
    with transaction_context() as connection:
        # Serialize with claimers and concurrent retries before reading checkpoints.
        connection.execute(update(dispatch).where(dispatch.c.id == '__retry_lock__').values(status='queued'))
        row = connection.execute(select(dispatch).where(dispatch.c.id == identifier,
            dispatch.c.tenant_id == context.tenant_id).with_for_update()).mappings().first()
        if not row or row['user_id'] != context.user_id or row['store_id'] != context.store_id:
            raise HTTPException(403, '无法继续这次设计')
        body = load_body('generation_job', identifier, action='write')
        if row['status'] != 'failed' or body.get('operation') != 'design' or body.get('committed_result'):
            raise HTTPException(409, '这次任务无需恢复，请查看当前结果')
        parameters = body.get('parameters') or {}
        design_id, source_id = parameters.get('design_id'), parameters.get('source_run_id')
        if not design_id or not source_id:
            raise HTTPException(409, '这次设计没有可接续的原图')
        validate_task_resources(TaskInput.model_validate(body['task']))
        source_record(source_id)  # Revalidate current source permission and live task grants.
        work = load_body('design_work', design_id, action='write')
        if work.get('source_run_id') != source_id:
            raise HTTPException(409, '设计来源已变化，请重新开始')
        stages = list((work.get('commercial_visuals') or {}).values())
        if not stages or any(stage.get('status') in {'started', 'unavailable'} for stage in stages):
            raise HTTPException(409, '原图请求结果尚未确认，请先核对已有请求')
        saved = False
        for stage in stages:
            asset = stage.get('asset') or {}
            if stage.get('status') != 'completed':
                continue
            try:
                header, encoded = str(asset.get('data_url') or '').split(',', 1)
                raw = base64.b64decode(encoded, validate=True)
                valid = header == 'data:image/png;base64' and raw.startswith(b'\x89PNG\r\n\x1a\n')
                if asset.get('sha256'):
                    valid = valid and hashlib.sha256(raw).hexdigest() == asset['sha256']
                saved = saved or valid
            except (ValueError, TypeError):
                continue
        if not saved:
            raise HTTPException(409, '这次设计没有可接续的原图')
        changed = connection.execute(update(dispatch).where(dispatch.c.id == identifier,
            dispatch.c.tenant_id == context.tenant_id, dispatch.c.user_id == context.user_id,
            dispatch.c.store_id == context.store_id, dispatch.c.status == 'failed').values(
                status='queued', claim_token=None, lease_until=0, session_hash=context.session_hash))
        if changed.rowcount != 1:
            raise HTTPException(409, '任务状态刚刚更新，请刷新查看')
        if body.get('error'):
            body['error_history'] = [*(body.get('error_history') or []),
                {'error': body['error'], 'finished_at': body.get('finished_at'), 'retried_at': time.time()}][-10:]
        body.update(status='queued', retry_count=int(body.get('retry_count') or 0) + 1, retried_at=time.time())
        for key in ('error', 'result', 'finished_at'):
            body.pop(key, None)
        save_body('generation_job', identifier, body)
        audit('job.retry', 'generation_job', identifier)
    return {'id': identifier, 'status': 'queued'}


def commit_artifact_result(kind, identifier, artifact, projected_result):
    """Fence and checkpoint a non-copy artifact with its usage reservation."""
    from .tenancy.database import transaction_context
    context = require_context()
    if kind != 'design':
        raise ValueError('Unsupported artifact')
    with transaction_context() as connection:
        claim = _active_claim.get()
        if claim:
            current = connection.execute(select(dispatch).where(dispatch.c.id == claim[0],
                dispatch.c.tenant_id == context.tenant_id).with_for_update()).mappings().first()
            if not current or current['claim_token'] != claim[1] or current['lease_until'] <= time.time() or current['status'] != 'running':
                raise HTTPException(409, '任务正在由新的工作进程接续')
        save_body(kind, identifier, artifact)
        plan = artifact.get('plan') or {}
        selected = next((item for item in plan.get('concepts', []) if item.get('id') == plan.get('selected_concept_id')), None)
        if selected:
            save_body('design_style', identifier, {'id': identifier, 'source_run_id': artifact['source_run_id'],
                'platform': artifact['platform'], 'status': artifact['status'], 'concept': selected})
        from .gtm_agent.effects import checkpoint_generated_artifact
        checkpoint_generated_artifact(kind, identifier, artifact)
        if claim:
            job = load_body('generation_job', claim[0], action='write')
            job['committed_result'] = projected_result
            save_body('generation_job', claim[0], job)
        reserved = _active_reservation.get()
        if reserved:
            connection.execute(update(usage).where(usage.c.id == reserved,
                usage.c.tenant_id == context.tenant_id, usage.c.status == 'reserved')
                .values(status='completed', finished_at=time.time()))


@router.get('/usage')
def usage_summary():
    context = require_context()
    guest, limit = _budget_scope(context)
    conditions = [usage.c.tenant_id == context.tenant_id]
    if not guest:
        conditions.append(usage.c.day == _day())
    with db_connection() as connection:
        rows = connection.execute(select(usage.c.status, func.count().label('count')).where(
            *conditions).group_by(usage.c.status)).mappings().all()
    counts = {row['status']: row['count'] for row in rows}
    return {'day': _day(), 'unit': 'creation', 'limit': limit,
            'completed': counts.get('completed', 0), 'reserved': counts.get('reserved', 0),
            'failed': counts.get('failed', 0)}


def _claim(previous_tenant=None):
    now = time.time()
    with db_connection() as connection:
        if connection.dialect.name == 'postgresql':
            from sqlalchemy import text
            connection.execute(text("SELECT pg_advisory_xact_lock(hashtext('matequill_job_dispatch'))"))
        else:
            connection.execute(update(dispatch).where(dispatch.c.id == '__claim_lock__').values(status='queued'))
        candidates = connection.execute(select(dispatch).where(
            (dispatch.c.status == 'queued') | ((dispatch.c.status == 'running') & (dispatch.c.lease_until < now)))
            .order_by(dispatch.c.created_at).limit(100)).mappings().all()
        # Round-robin tenants when another tenant is waiting.
        ordered = sorted(candidates, key=lambda row: row['tenant_id'] == previous_tenant)
        candidate = None
        for option in ordered:
            active = connection.execute(select(func.count()).select_from(dispatch).where(
                dispatch.c.tenant_id == option['tenant_id'], dispatch.c.status == 'running',
                dispatch.c.lease_until >= now)).scalar_one()
            if active < 1:
                candidate = option
                break
        if candidate is None:
            return None
        claim_token = uuid4().hex
        result = connection.execute(update(dispatch).where(dispatch.c.id == candidate['id'],
            (dispatch.c.status == 'queued') | (dispatch.c.lease_until < now)).values(
                status='running', lease_until=now + 240, claim_token=claim_token))
        return {**dict(candidate), 'claim_token': claim_token} if result.rowcount == 1 else None


def assert_job_lease():
    claim = _active_claim.get()
    if not claim:
        return
    with db_connection() as connection:
        valid = connection.execute(select(dispatch.c.id).where(dispatch.c.id == claim[0],
            dispatch.c.claim_token == claim[1], dispatch.c.status == 'running',
            dispatch.c.lease_until > time.time())).first()
    if not valid:
        raise HTTPException(409, '任务已由新的工作进程接续，请刷新查看结果')


def _renew(row):
    with db_connection() as connection:
        result = connection.execute(update(dispatch).where(dispatch.c.id == row['id'],
            dispatch.c.tenant_id == row['tenant_id'], dispatch.c.claim_token == row['claim_token'],
            dispatch.c.status == 'running', dispatch.c.lease_until > time.time()).values(lease_until=time.time() + 240))
    return result.rowcount == 1


def _finish(row, body, final):
    from .tenancy.database import transaction_context
    with transaction_context() as connection:
        current = connection.execute(select(dispatch).where(dispatch.c.id == row['id'],
            dispatch.c.tenant_id == row['tenant_id']).with_for_update()).mappings().first()
        if not current or current['claim_token'] != row['claim_token'] or current['status'] != 'running':
            return False
        if final != 'queued' and current['lease_until'] <= time.time():
            return False
        if body is not None:
            if final != 'succeeded':
                persisted = load_body('generation_job', row['id'], action='write')
                if persisted.get('committed_result'):
                    # Shutdown may interrupt the parent after the child has
                    # committed its run, but before its response is delivered.
                    body = {**body, 'committed_result': persisted['committed_result']}
            save_body('generation_job', row['id'], body)
        changes = {'status': final}
        if final != 'running':
            changes['lease_until'] = 0
        connection.execute(update(dispatch).where(dispatch.c.id == row['id'],
            dispatch.c.claim_token == row['claim_token']).values(**changes))
    return True


async def execute_claimed(row):
    original = TenantContext(user_id=row['user_id'], tenant_id=row['tenant_id'],
        store_id=row['store_id'], session_hash=row['session_hash'])
    context = refresh_context(original)
    if context is None:
        with db_connection() as connection:
            connection.execute(update(dispatch).where(dispatch.c.id == row['id'],
                dispatch.c.claim_token == row['claim_token']).values(status='cancelled', lease_until=0))
        return
    token = set_context(context)
    job_token = _active_job.set(row['id'])
    claim_context = _active_claim.set((row['id'], row['claim_token']))
    final = 'failed'
    body = None
    operation = None
    heartbeat = None
    stopped = {}
    progress_token = None
    try:
        assert_job_lease()
        if row['status'] == 'running':
            from .tenancy.database import transaction_context
            with transaction_context() as connection:
                current = connection.execute(select(dispatch).where(dispatch.c.id == row['id'])
                    .with_for_update()).mappings().first()
                if not current or current['claim_token'] != row['claim_token'] or current['lease_until'] <= time.time():
                    return
                connection.execute(update(usage).where(usage.c.tenant_id == row['tenant_id'],
                    usage.c.id.like(row['id'] + ':%'), usage.c.status == 'reserved')
                    .values(status='failed', finished_at=time.time()))
        body = load_body('generation_job', row['id'], action='write')
        if body.get('status') == 'succeeded':
            final = 'succeeded'
            return
        if body.get('committed_result'):
            from .resource_access import validate_task_resources
            validate_task_resources(TaskInput.model_validate(body['task']))
            body.update(status='succeeded', result=body.pop('committed_result'), finished_at=time.time())
            final = 'succeeded'
            return
        body['status'] = 'running'
        if not _finish(row, body, 'running'):
            return
        if not _renew(row):
            return
        from .api import run_task
        from . import job_progress
        request = Request({'type': 'http', 'method': 'POST', 'path': '/api/run',
            'headers': [], 'client': ('127.0.0.1', 0), 'server': ('127.0.0.1', 8000), 'scheme': 'http'})
        operation_name = body.get('operation', 'content')
        progress_token = job_progress.bind(row['id'], operation_name)
        if operation_name == 'design':
            from .design_service import execute_design
            operation = asyncio.create_task(execute_design(body.get('parameters') or {}))
        elif operation_name == 'poster':
            from .poster_jobs import execute_poster_job
            operation = asyncio.create_task(execute_poster_job(body.get('parameters') or {}, request))
        else:
            operation = asyncio.create_task(run_task(TaskInput.model_validate(body['task']), request))
        async def keep_alive():
            while not operation.done():
                await asyncio.sleep(20)
                if refresh_context(context) is None:
                    stopped['reason'] = 'revoked'
                    operation.cancel()
                    return
                if not await asyncio.to_thread(_renew, row):
                    stopped['reason'] = 'lease_lost'
                    operation.cancel()
                    return
                try:
                    if load_body('generation_job', row['id']).get('cancel_requested'):
                        stopped['reason'] = 'cancel_requested'
                        operation.cancel()
                        return
                except Exception:
                    pass
        heartbeat = asyncio.create_task(keep_alive())
        response = await operation
        if refresh_context(context) is None:
            final = 'cancelled'
            body.update(status=final, error={'message': '任务权限已更新，请重新开始创作'})
            return
        body.update(status='succeeded', result=json.loads(response.body), finished_at=time.time())
        body.pop('committed_result', None)
        final = 'succeeded'
    except asyncio.CancelledError:
        final = 'cancelled' if stopped.get('reason') else 'queued'
        if body is not None:
            body.update(status=final)
            body.pop('result', None)
            if final == 'cancelled':
                message = ('已按你的要求停止这次创作' if stopped.get('reason') == 'cancel_requested'
                           else '任务权限已更新，请重新开始创作')
                body['error'] = {'message': message}
    except Exception as exc:
        if refresh_context(context) is None:
            final = 'cancelled'
            if body is not None:
                body.update(status=final, error={'message': '任务权限已更新，请重新开始创作'})
        elif body is not None:
            # HTTPException.detail is not guaranteed to be a string (many
            # call sites raise a structured {'code': ..., ...} payload with
            # no human-readable 'message'); the polled error.message must
            # always render as text, never a raw object.
            detail = getattr(exc, 'detail', None) if isinstance(exc, HTTPException) else None
            if isinstance(detail, str) and detail:
                message = detail
            elif isinstance(detail, dict) and isinstance(detail.get('message'), str):
                message = detail['message']
            else:
                message = '这次创作未完成，请重试'
            error = {'message': message}
            # Diagnose by structure only (error type, field path, declared
            # limit, or the HTTPException's own machine code); never the
            # offending text, so this stays safe to log.
            if isinstance(exc, ValidationError):
                error['diagnostic'] = {'type': 'ValidationError',
                    'fields': [{'path': list(item['loc']), 'type': item['type']}
                               for item in exc.errors(include_input=False)[:12]]}
            elif isinstance(detail, dict) and isinstance(detail.get('code'), str):
                error['diagnostic'] = {'type': 'HTTPException', 'code': detail['code'][:80]}
            elif isinstance(exc, HTTPException):
                error['diagnostic'] = {'type': 'HTTPException', 'status_code': exc.status_code}
            else:
                # Any other exception type/class name is implementation
                # metadata, not customer content; safe to keep for triage.
                error['diagnostic'] = {'type': type(exc).__name__}
                import traceback
                frames = [frame for frame in traceback.extract_tb(exc.__traceback__)
                          if os.sep + 'app' + os.sep in frame.filename]
                if frames:
                    error['diagnostic']['at'] = (
                        f"{os.path.basename(frames[-1].filename)}:{frames[-1].lineno}")
            body.update(status='failed', error=error, finished_at=time.time())
    finally:
        if heartbeat:
            heartbeat.cancel()
            await asyncio.gather(heartbeat, return_exceptions=True)
        if operation and not operation.done():
            operation.cancel()
            await asyncio.gather(operation, return_exceptions=True)
        try:
            _finish(row, body, final)
        finally:
            if progress_token is not None:
                from . import job_progress
                job_progress.unbind(progress_token)
            reset_context(token)
            _active_job.reset(job_token)
            _active_claim.reset(claim_context)


async def _worker():
    previous = None
    while not _stop.is_set():
        try:
            row = await asyncio.to_thread(_claim, previous)
            if row:
                previous = row['tenant_id']
                await execute_claimed(row)
                continue
        except Exception:
            # Keep polling on a transient DB failure; no customer data in logs.
            pass
        try:
            await asyncio.wait_for(_stop.wait(), timeout=1)
        except asyncio.TimeoutError:
            pass


async def start_workers():
    global _stop
    _stop = asyncio.Event()
    for _ in range(2):
        worker = asyncio.create_task(_worker())
        _workers.add(worker)
        worker.add_done_callback(_workers.discard)


async def stop_workers():
    if _stop:
        _stop.set()
    for worker in tuple(_workers):
        worker.cancel()
    await asyncio.gather(*tuple(_workers), return_exceptions=True)
