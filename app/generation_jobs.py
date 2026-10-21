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

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field
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


def _day():
    return datetime.now(timezone.utc).date().isoformat()


@contextmanager
def generation_budget():
    context = get_context()
    if context is None or context.is_legacy:
        yield
        return
    identifier = ((_active_job.get() + ':') if _active_job.get() else '') + uuid4().hex[:24]
    limit = int(os.getenv('MATEQUILL_DAILY_CREATIONS', '100'))
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
        count = connection.execute(select(func.count()).select_from(usage).where(
            usage.c.tenant_id == context.tenant_id, usage.c.day == _day(),
            usage.c.status.in_(['reserved', 'completed']))).scalar_one()
        if count >= limit:
            raise HTTPException(429, '今日创作额度已用完，请联系企业管理员')
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
        if claim:
            job = load_body('generation_job', claim[0], action='write')
            job['committed_result'] = record
            save_body('generation_job', claim[0], job)
        reserved = _active_reservation.get()
        if reserved:
            connection.execute(update(usage).where(usage.c.id == reserved,
                usage.c.tenant_id == context.tenant_id, usage.c.status == 'reserved')
                .values(status='completed', finished_at=time.time()))


class JobInput(BaseModel):
    task: TaskInput
    idempotency_key: str = Field(default_factory=lambda: uuid4().hex, min_length=8, max_length=100)


@router.post('/jobs', status_code=202)
def enqueue(payload: JobInput):
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
                if prior_body.get('task') != payload.task.model_dump(mode='json'):
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
    return {key: value for key, value in body.items() if key not in {'task', 'committed_result'}}


@router.get('/usage')
def usage_summary():
    context = require_context()
    with db_connection() as connection:
        rows = connection.execute(select(usage.c.status, func.count().label('count')).where(
            usage.c.tenant_id == context.tenant_id, usage.c.day == _day()).group_by(usage.c.status)).mappings().all()
    counts = {row['status']: row['count'] for row in rows}
    return {'day': _day(), 'unit': 'creation', 'limit': int(os.getenv('MATEQUILL_DAILY_CREATIONS', '100')),
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
        request = Request({'type': 'http', 'method': 'POST', 'path': '/api/run',
            'headers': [], 'client': ('127.0.0.1', 0), 'server': ('127.0.0.1', 8000), 'scheme': 'http'})
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
                body['error'] = {'message': '任务权限已更新，请重新开始创作'}
    except Exception as exc:
        if refresh_context(context) is None:
            final = 'cancelled'
            if body is not None:
                body.update(status=final, error={'message': '任务权限已更新，请重新开始创作'})
        elif body is not None:
            body.update(status='failed', error={'message': exc.detail if isinstance(exc, HTTPException) else '这次创作未完成，请重试'}, finished_at=time.time())
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
