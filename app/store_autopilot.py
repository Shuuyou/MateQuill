"""Turn a persisted store opportunity into one recoverable real generation job.

Only server-persisted preparation results may reach the dispatcher. This module
does not run providers itself or publish to social accounts: durable jobs retain
the existing membership, campaign, lease and budget checks.
"""
import hashlib
import json
import time

from fastapi import HTTPException
from sqlalchemy import update

from . import channel_workflows as channel
from . import generation_jobs as jobs
from .models import TaskInput
from .tenancy import require_context, load_body, save_body
from .tenancy.database import resources, transaction_context


def opportunity_cache_key(assignment_id: str, campaign_version: int) -> str:
    context = require_context()
    return hashlib.sha256(json.dumps(['opportunity-v3', context.tenant_id,
        context.user_id, context.store_id, assignment_id, campaign_version],
        separators=(',', ':')).encode()).hexdigest()


def _scope(assignment_id):
    context = require_context()
    _, assignment, campaign = channel._assignment(assignment_id)
    if not context.store_id or context.store_id != assignment['store_id']:
        raise HTTPException(409, '先切换到这家门店，再准备本店内容')
    return context, assignment, campaign, opportunity_cache_key(assignment_id, campaign['version'])


def _optional(kind, identifier):
    try:
        return load_body(kind, identifier)
    except HTTPException as exc:
        if exc.status_code != 404:
            raise
        return None


def _view(record):
    job = jobs.job_result(record['job_id'])
    state = job['status']
    result = job.get('result') if state == 'succeeded' else None
    status = state
    if state == 'succeeded':
        status = 'ready' if isinstance(result, dict) and result.get('status') == 'ready' else 'needs_revision'
    view = {'job_id': record['job_id'], 'status': status,
        'generation_status': state, 'attempt': record['attempt'],
        'campaign_version': record['campaign_version'], 'created_at': record['created_at']}
    # This is the exact server-issued task for this job, even if another angle
    # has since replaced the opportunity cache while generation was in flight.
    if record.get('task') is not None:
        view['task'] = record['task']
    if record.get('opportunity') is not None:
        view['opportunity'] = record['opportunity']
    if result is not None:
        view['result'] = result
        if isinstance(result, dict) and result.get('run_id'):
            view['run_id'] = result['run_id']
    if job.get('error'):
        view['error'] = job['error']
    return view


def read_store_draft(assignment_id: str):
    """Read the current user's latest draft for the live assignment version."""
    _, _, _, key = _scope(assignment_id)
    record = _optional('store_autopilot', key)
    return _view(record) if record else None


async def ensure_store_draft(assignment_id: str, prepared: dict, *, refresh=False):
    """Queue once after preparation; refresh retries only terminal failures.

    Caller must persist the result under ``opportunity_cache_key`` first. A
    supplied task cannot impersonate the autonomous planner, even when its
    campaign fields happen to be valid. All mutations share the queue's DB
    transaction, so concurrent calls and process restarts reuse the same job.
    """
    context, assignment, campaign, key = _scope(assignment_id)
    with transaction_context() as connection:
        if connection.dialect.name == 'postgresql':
            from sqlalchemy import text
            connection.execute(text('SELECT pg_advisory_xact_lock(hashtext(:key))'), {'key': key})
        else:
            connection.execute(update(resources).where(resources.c.kind == '__autopilot_lock__')
                .values(updated_at=time.time()))
        # Re-read after the serialization boundary: a concurrent refresh may
        # have replaced the opportunity before this invocation acquired it.
        _, assignment, campaign, current_key = _scope(assignment_id)
        if current_key != key:
            raise HTTPException(409, '活动资料已更新，请重新准备内容')
        cached = _optional('store_opportunity', key)
        supplied = {name: value for name, value in prepared.items() if name != 'cache_hit'}
        if (not cached or cached.get('assignment_id') != assignment_id
                or cached.get('campaign_version') != campaign['version']
                or cached.get('result') != supplied
                or supplied.get('status') != 'ready'):
            raise HTTPException(409, '请先完成本店选题准备，再开始创作')
        task = TaskInput.model_validate(cached['result']['task'])
        if task.assignment_id != assignment_id:
            raise HTTPException(422, '创作需求与门店活动任务不一致')
        channel.validate_generation_task(task)
        digest = hashlib.sha256(json.dumps(cached['result'], sort_keys=True,
            ensure_ascii=False, separators=(',', ':')).encode()).hexdigest()
        prior = _optional('store_autopilot', key)
        attempt = 1
        if prior:
            view = _view(prior)
            if not refresh and view['status'] in {'failed', 'cancelled', 'needs_revision'}:
                return view
            if prior['preparation_hash'] == digest:
                if not refresh or view['status'] not in {'failed', 'cancelled', 'needs_revision'}:
                    return view
            elif view['status'] in {'queued', 'running'}:
                # Finish in-flight work before starting a different angle.
                return view
            attempt = prior['attempt'] + 1
        channel.accept_assignment(assignment_id)
        dispatched = jobs.enqueue(jobs.JobInput(task=task,
            idempotency_key='autopilot:' + hashlib.sha256(f'{key}:{digest}:{attempt}'.encode()).hexdigest()))
        record = {'assignment_id': assignment_id, 'campaign_version': campaign['version'],
            'preparation_hash': digest, 'job_id': dispatched['id'], 'attempt': attempt,
            'task': task.model_dump(mode='json'), 'opportunity': cached['result'].get('opportunity'),
            'created_at': time.time()}
        save_body('store_autopilot', key, record, store_id=assignment['store_id'], visibility='private')
        return _view(record)
