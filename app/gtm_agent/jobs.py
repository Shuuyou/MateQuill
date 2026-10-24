"""Durable, leased turns. Private content remains in the scoped resource store."""
import asyncio
import json
import time
from uuid import uuid4

from fastapi import HTTPException
from sqlalchemy import Column, Float, Integer, String, Table, select, update, text, func

from ..tenancy import (TenantContext, require_context, authorize_resource, register_resource,
    service_update_body, transaction_context, set_context, reset_context, refresh_context)
from ..tenancy.database import metadata, db_connection, resources
from .models import TurnInput
from .repository import (CONVERSATION, TURN, conversation_row, turn_id, live_context,
    artifact_reference, get_turn)
from .effects import _claim, assert_agent_lease

dispatch = Table('mq_agent_dispatch', metadata,
    Column('id', String(64), primary_key=True), Column('tenant_id', String(64), nullable=False),
    Column('user_id', String(64), nullable=False), Column('store_id', String(120), nullable=False),
    Column('session_hash', String(64), nullable=False), Column('status', String(24), nullable=False),
    Column('claim_token', String(64)), Column('lease_until', Float, nullable=False, default=0),
    Column('created_at', Float, nullable=False), Column('attempts', Integer, nullable=False, default=0))
_workers = set()
_stop = None
AGENT_LEASE_SECONDS = 260
HEARTBEAT_INTERVAL = 20


def enqueue_turn(conversation_id, payload: TurnInput, *, origin='user'):
    if origin not in {'user', 'daily_store_agent'}:
        raise ValueError('Unknown trusted turn origin')
    context = live_context()
    if not payload.text.strip() and not payload.media_ids:
        raise HTTPException(422, '说一句这次想做什么，或者发一张照片')
    identifier = turn_id(conversation_id, payload.client_turn_id)
    with transaction_context() as connection:
        row = conversation_row(conversation_id, editing=True)
        connection.execute(select(resources.c.id).where(resources.c.kind == CONVERSATION,
            resources.c.id == conversation_id).with_for_update())
        existing = connection.execute(select(resources).where(resources.c.kind == TURN,
            resources.c.id == identifier, resources.c.tenant_id == context.tenant_id)).mappings().first()
        incoming = payload.model_dump(exclude={'expected_version'})
        if existing:
            body = json.loads(existing['body'])
            if body.get('input') != incoming or body.get('input_origin', 'user') != origin:
                raise HTTPException(409, '这次消息已经提交，修改后请作为新消息发送')
            return get_turn(conversation_id,identifier)
        if payload.expected_version is not None and payload.expected_version != row['version']:
            raise HTTPException(409, '对话刚刚有更新，请刷新后继续')
        if row['body'].get('active_turn_id'):
            active = connection.execute(select(dispatch.c.status).where(
                dispatch.c.id == row['body']['active_turn_id'])).scalar_one_or_none()
            if active in {'queued','running'}:
                raise HTTPException(409, '上一条消息正在处理，完成后可以接着说')
        pending = connection.execute(select(func.count()).select_from(dispatch).where(
            dispatch.c.tenant_id == context.tenant_id,dispatch.c.status.in_(['queued','running']))).scalar_one()
        if pending >= 20:
            raise HTTPException(429, '正在准备的任务较多，稍后再发这条消息')
        for media_id in payload.media_ids:
            meta = authorize_resource('media',media_id)
            if meta['store_id'] != context.store_id or (row['body']['audience'] == 'group' and meta['visibility'] == 'private'):
                raise HTTPException(404, '请使用这家门店可以访问的素材')
        reference = payload.reply_to_artifact.model_dump() if payload.reply_to_artifact else None
        if reference:
            actual = artifact_reference(reference['kind'],reference['id'],audience=row['body']['audience'])
            if reference.get('version') is not None and reference['version'] != actual['version']:
                raise HTTPException(409, '这份作品刚刚有更新，请先查看最新版本')
            reference = actual
        now = time.time()
        body = {'id':identifier,'conversation_id':conversation_id,'status':'queued','text':payload.text,'input_origin':origin,
            'media_ids':payload.media_ids,'reply_to_artifact':reference,'input':incoming,
            'message':'','questions':[],'artifact':None,'effects':{},'observations':[],
            'created_at':now,'updated_at':now}
        register_resource(TURN,identifier,body=body,visibility=row['visibility'])
        from ..work_signals import capture
        if origin == 'user':
            capture(TURN, identifier, payload.text, audience=row['body']['audience'], now=now)
        conversation = {**row['body'],'active_turn_id':identifier,'pending_questions':[],
            'last_turn_id':identifier,'updated_at':now}
        if reference:
            conversation['current_artifact'] = reference
        conversation['messages'] = (conversation['messages'] + [{'id':identifier+':user','role':'user',
            'text':payload.text,'media_ids':payload.media_ids,'created_at':now}])[-100:]
        service_update_body(CONVERSATION,conversation_id,conversation,expected_version=row['version'])
        connection.execute(dispatch.insert().values(id=identifier,tenant_id=context.tenant_id,
            user_id=context.user_id,store_id=context.store_id,session_hash=context.session_hash,
            status='queued',lease_until=0,created_at=now,attempts=0))
    return get_turn(conversation_id,identifier)


def claim_next():
    with db_connection() as connection:
        if connection.dialect.name == 'postgresql':
            connection.execute(text("SELECT pg_advisory_xact_lock(hashtext('agent-dispatch'))"))
        else:
            connection.execute(update(dispatch).where(dispatch.c.id == '__claim_lock__').values(status='queued'))
        now = time.time()
        eligible = ((dispatch.c.status == 'queued') | ((dispatch.c.status == 'running') & (dispatch.c.lease_until < now)))
        row = connection.execute(select(dispatch).where(eligible).order_by(dispatch.c.created_at).limit(1)).mappings().first()
        if not row:
            return None
        token = uuid4().hex
        connection.execute(update(dispatch).where(dispatch.c.id == row['id']).values(
            status='running',claim_token=token,lease_until=now+AGENT_LEASE_SECONDS,attempts=row['attempts']+1))
        return {**dict(row),'status':'running','claim_token':token,'attempts':row['attempts']+1}


def _renew(row):
    """A stale owner cannot revive an expired or replaced lease."""
    now = time.time()
    with db_connection() as connection:
        result = connection.execute(update(dispatch).where(
            dispatch.c.id == row['id'], dispatch.c.tenant_id == row['tenant_id'],
            dispatch.c.claim_token == row['claim_token'], dispatch.c.status == 'running',
            dispatch.c.lease_until > now).values(lease_until=now+AGENT_LEASE_SECONDS))
    return result.rowcount == 1


def _release_owned(row, status):
    """Release only the still-live claim; never overwrite another worker."""
    with db_connection() as connection:
        result = connection.execute(update(dispatch).where(
            dispatch.c.id == row['id'], dispatch.c.tenant_id == row['tenant_id'],
            dispatch.c.claim_token == row['claim_token'], dispatch.c.status == 'running',
            dispatch.c.lease_until > time.time()).values(status=status, lease_until=0))
    return result.rowcount == 1


def finish_turn(row, outcome):
    with transaction_context() as connection:
        assert_agent_lease()
        turn = authorize_resource(TURN,row['id'])
        conversation = conversation_row(turn['body']['conversation_id'],editing=True)
        if outcome.get('quotation'):
            from .runtime import quotation_delivery
            live_quote = quotation_delivery(row['id'], turn['body'].get('execution_policy') or {})
            if not live_quote or live_quote.get('quotation') != outcome['quotation']:
                outcome = live_quote or {'status': 'needs_input', 'message': '请重新核验这份报价。',
                                        'artifact': None, 'quotation': None, 'questions': []}
        saved = turn['body'].get('artifact')
        delivered = outcome.get('artifact') or saved
        plan = conversation['body'].get('current_opportunity') or {}
        applies = plan.get('turn_id') == row['id'] or bool(delivered and
            any(item.get('id') == delivered['id'] for item in plan.get('artifacts', [])))
        scope = turn['body'].get('delivery_scope')
        requested_images = 'images' in scope if isinstance(scope, list) else (
            applies and 'images' in plan.get('deliverables', []))
        missing_images = bool(requested_images and delivered and delivered.get('kind') == 'run')
        delivery_error = (outcome.get('error') or {}).get('code') in {
            'partial_delivery', 'images_pending', 'image_attempt_stopped', 'image_response_invalid'}
        if missing_images and outcome['status'] in {'completed', 'failed'}:
            outcome = {**outcome, 'status':'failed',
                'message':'文案与体验方案已保存，配图尚未完成，可以接着制作图片。',
                'artifact':delivered, 'questions':[],
                'error':{'code':'partial_delivery','missing':['images'],
                    'reason':(outcome.get('error') or {}).get('code', 'images_pending')}}
        elif saved and outcome['status'] == 'failed':
            reference = artifact_reference(saved['kind'], saved['id'], audience=conversation['body']['audience'])
            if delivery_error:
                # A committed text checkpoint does not make an unsuccessful
                # image operation complete. Preserve it without losing failure.
                outcome = {**outcome, 'artifact':reference}
            else:
                # Preserve successful single-artifact work whose transport
                # response was lost after commit, as before.
                outcome = {'status':'completed','message':'作品已经准备好，可以继续修改。',
                    'artifact':reference,'questions':[]}
        body = {**turn['body'],**outcome,'updated_at':time.time()}
        service_update_body(TURN,row['id'],body,expected_version=turn['version'])
        current = conversation['body']
        if current.get('active_turn_id') != row['id']:
            raise HTTPException(409, '对话已由新的任务继续')
        current = {**current,'active_turn_id':None,'last_turn_id':row['id'],
            'pending_questions':outcome.get('questions',[]) if outcome['status']=='needs_input' else [],
            'updated_at':time.time()}
        current['messages'] = (current['messages']+[{'id':row['id']+':assistant','role':'assistant',
            'text':outcome.get('message',''),'artifact':outcome.get('artifact'),
            'quotation':outcome.get('quotation'),'created_at':time.time()}])[-100:]
        if outcome.get('artifact'):
            current['current_artifact'] = outcome['artifact']
        service_update_body(CONVERSATION,current['id'],current,expected_version=conversation['version'])
        connection.execute(update(dispatch).where(dispatch.c.id == row['id'],dispatch.c.claim_token == row['claim_token'])
            .values(status=outcome['status'],lease_until=0))


async def execute_claimed(row, *, planner=None, tools_factory=None):
    context = refresh_context(TenantContext(row['user_id'],row['tenant_id'],store_id=row['store_id'],session_hash=row['session_hash']))
    if context is None:
        _release_owned(row, 'cancelled')
        return
    context_token = set_context(context)
    claim_token = _claim.set((row['id'],row['claim_token']))
    operation = None
    heartbeat = None
    stopped = {}
    try:
        from .runtime import run_turn
        if not _renew(row):
            return
        if row['attempts'] > 3:
            finish_turn(row,{'status':'failed','message':'这次任务已暂停，可以重新发一条消息继续。','artifact':None,'questions':[]})
            return
        operation = asyncio.create_task(run_turn(row['id'],planner=planner,tools_factory=tools_factory))

        async def keep_alive():
            try:
                while not operation.done():
                    await asyncio.sleep(HEARTBEAT_INTERVAL)
                    if operation.done():
                        return
                    if await asyncio.to_thread(refresh_context, context) is None:
                        stopped['reason'] = 'revoked'
                        operation.cancel()
                        return
                    if not await asyncio.to_thread(_renew, row):
                        stopped['reason'] = 'lease_lost'
                        operation.cancel()
                        return
            except asyncio.CancelledError:
                raise
            except Exception:
                # An unavailable heartbeat must not leave unowned model work
                # running. Let the lease expire for a fenced recovery.
                stopped['reason'] = 'heartbeat_failed'
                operation.cancel()

        heartbeat = asyncio.create_task(keep_alive())
        result = await operation
        live_context()
        finish_turn(row,result)
    except asyncio.CancelledError:
        reason = stopped.get('reason')
        if reason == 'revoked':
            _release_owned(row, 'cancelled')
        elif not reason:
            # Shutdown/interruption retains any committed artifact checkpoint.
            # Lost or expired owners cannot requeue the replacement's work.
            _release_owned(row, 'queued')
            raise
    except Exception as exc:
        try:
            live_context()
            # Machine-readable provider codes contain no response bodies or secrets.
            import logging
            import re
            code = getattr(exc, 'code', type(exc).__name__)
            code = code if isinstance(code, str) and re.fullmatch(r'[A-Za-z0-9_]{1,80}', code) else type(exc).__name__
            logging.getLogger(__name__).warning('Agent turn failed: %s', code)
            finish_turn(row,{'status':'failed','message':'这次没有完成，你可以保留这段对话继续。',
                'artifact':None,'questions':[],'error':{'code':code}})
        except Exception:
            _release_owned(row, 'cancelled')
    finally:
        if heartbeat:
            heartbeat.cancel()
            await asyncio.gather(heartbeat, return_exceptions=True)
        if operation and not operation.done():
            operation.cancel()
            await asyncio.gather(operation, return_exceptions=True)
        _claim.reset(claim_token)
        reset_context(context_token)


async def _worker():
    while not _stop.is_set():
        try:
            row = await asyncio.to_thread(claim_next)
            if row:
                await execute_claimed(row)
                continue
        except asyncio.CancelledError:
            raise
        except Exception:
            pass
        try:
            await asyncio.wait_for(_stop.wait(),timeout=1)
        except asyncio.TimeoutError:
            pass


async def start_agent_workers():
    global _stop
    _stop = asyncio.Event()
    worker = asyncio.create_task(_worker())
    _workers.add(worker)
    worker.add_done_callback(_workers.discard)


async def stop_agent_workers():
    if _stop:
        _stop.set()
    for worker in tuple(_workers):
        worker.cancel()
    await asyncio.gather(*tuple(_workers),return_exceptions=True)
