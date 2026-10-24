"""Durable inbox/outbox bridging official channels to the shared Agent service."""
import asyncio
import json
import os
import re
import time
import logging
from uuid import uuid4
from fastapi import HTTPException
from sqlalchemy import select, update, or_
from sqlalchemy.exc import IntegrityError
from app.tenancy.database import db_connection
from app.tenancy import load_body, save_body, transaction_context
from .identity import as_binding, bind_code, create_code, digest, find_binding, resolve_identity, thread_key
from .models import InboundEvent
from .tables import inbox, outbox, threads, connections, bindings
from .config import web_origin

log=logging.getLogger(__name__)


def connection_state(config,status):
    with db_connection() as connection:
        values=dict(channel=config.channel,status=status,heartbeat_at=time.time())
        found=connection.execute(select(connections.c.id).where(connections.c.id==config.connection_id)).first()
        if found:
            connection.execute(update(connections).where(connections.c.id==config.connection_id).values(**values))
        else:
            connection.execute(connections.insert().values(id=config.connection_id,**values))


def group_allowed(binding,event):
    if event.audience=='private':
        return True
    try:
        scopes=json.loads(os.getenv('MATEQUILL_MESSAGING_GROUP_SCOPES','{}'))
        target=scopes.get(event.connection_id+':'+event.chat_id,{})
        return target.get('tenant_id')==binding['tenant_id'] and target.get('store_id')==binding['store_id']
    except (ValueError,TypeError):
        return False


class Gateway:
    def __init__(self,transport):
        self.transport=transport

    async def ingest(self,event:InboundEvent):
        if not event.addressed or event.connection_id!=self.transport.config.connection_id:
            return False
        # Identity creation and DB work are bounded, no model/network work before ACK.
        return await asyncio.to_thread(self._persist,event)

    def _persist(self,event):
        if event.audience=='group':
            try:
                scopes=json.loads(os.getenv('MATEQUILL_MESSAGING_GROUP_SCOPES','{}'))
                if not scopes.get(event.connection_id+':'+event.chat_id):
                    return False
            except (ValueError,TypeError):
                return False
        identifier=digest(event.connection_id,event.event_id)
        with db_connection() as connection:
            if connection.execute(select(inbox.c.id).where(inbox.c.id==identifier)).first():
                return False
        command=re.fullmatch(r'绑定\s+([a-fA-F0-9]{16})',event.text.strip())
        binding=bind_code(event,command[1]) if command else resolve_identity(event)
        if not group_allowed(binding,event):
            return False # Never read private conversations from an untrusted group.
        with as_binding(binding),transaction_context() as connection:
            existing=connection.execute(select(inbox.c.id).where(inbox.c.id==identifier)).first()
            if existing:
                return False
            save_body('messaging_inbound',identifier,{'event':event.model_dump(),'media_ids':[],'bound':bool(command)})
            now=time.time()
            connection.execute(inbox.insert().values(id=identifier,binding_id=binding['id'],channel=event.channel,
                status='queued',lease_until=0,attempts=0,created_at=now,updated_at=now))
            connection.execute(update(bindings).where(bindings.c.id==binding['id']).values(last_seen_at=now))
        return True

    def claim(self,table):
        with db_connection() as connection:
            now=time.time()
            query=select(table).join(bindings,bindings.c.id==table.c.binding_id).where(
                bindings.c.connection_id==self.transport.config.connection_id,table.c.channel==self.transport.config.channel,
                table.c.status.in_(['queued','processing','awaiting_turn']),table.c.lease_until<now,
                table.c.attempts<12)
            if table is outbox:
                query=query.where(table.c.next_attempt_at<=now)
            row=connection.execute(query.order_by(table.c.created_at).limit(1)).mappings().first()
            if not row:
                return None
            token=uuid4().hex
            result=connection.execute(update(table).where(table.c.id==row['id'],table.c.lease_until<now,
                table.c.status==row['status']).values(claim_token=token,lease_until=now+180,
                attempts=table.c.attempts+1,status='processing'))
            return {**row,'claim_token':token} if result.rowcount==1 else None

    def update(self,table,row,**values):
        with db_connection() as connection:
            result=connection.execute(update(table).where(table.c.id==row['id'],
                table.c.claim_token==row['claim_token']).values(**values))
            if result.rowcount!=1:
                raise RuntimeError('message_lease_lost')

    def queue_reply(self,row,binding,event,text,cid=None,artifact=None):
        with transaction_context() as connection:
            save_body('messaging_outbound',row['id'],{'event':event.model_dump(),'text':text,'conversation_id':cid,'artifact':artifact})
            if not connection.execute(select(outbox.c.id).where(outbox.c.id==row['id'])).first():
                now=time.time()
                connection.execute(outbox.insert().values(id=row['id'],binding_id=binding['id'],channel=event.channel,
                    status='queued',attempts=0,lease_until=0,next_attempt_at=0,created_at=now,updated_at=now))
            self.update(inbox,row,status='done',lease_until=0,updated_at=time.time())

    async def process(self,row):
        binding=find_binding(row['binding_id'])
        with as_binding(binding):
            body=load_body('messaging_inbound',row['id'])
            event=InboundEvent.model_validate(body['event'])
            if not group_allowed(binding,event):
                raise HTTPException(403,'群工作空间已更新')
            if body.get('bound'):
                self.queue_reply(row,binding,event,'已连接工作空间。接着说这次想做什么，或发一张照片。')
                return
            from app.gtm_agent.service import create_conversation,enqueue_turn,get_turn,TurnInput
            cid=row.get('conversation_id')
            if not cid:
                key=thread_key(binding,event)
                with transaction_context() as connection:
                    found=connection.execute(select(threads.c.conversation_id).where(threads.c.id==key)).scalar_one_or_none()
                    cid=found or create_conversation('聊天里的创作',audience=event.audience)['id']
                    if not found:
                        connection.execute(threads.insert().values(id=key,binding_id=binding['id'],conversation_id=cid,updated_at=time.time()))
                    self.update(inbox,row,conversation_id=cid)
            if event.text.strip() in {'打开网页','继续编辑','重新生成链接'} and not event.attachments:
                from app.gtm_agent.service import get_conversation
                from .artifacts import artifact_text
                current=get_conversation(cid).get('current_artifact')
                text='从这里接着编辑，也可以继续在聊天里告诉我怎么改。'
                if current:
                    text+='\n\n'+artifact_text(current,event.audience)
                self.queue_reply(row,binding,event,text,cid,current)
                return
            media_ids=body.get('media_ids',[])
            if event.attachments and not body.get('media_processed'):
                from app.config import settings
                from app.media_intake import process_media
                from app.media_routes import media_store
                from app.tenancy import require_context
                from app.tenancy.auth import guest_state
                from app.tenancy.database import resources
                from sqlalchemy import func
                context=require_context()
                if guest_state(context.user_id,context.tenant_id)['is_guest']:
                    with db_connection() as connection:
                        count=connection.execute(select(func.count()).select_from(resources).where(
                            resources.c.tenant_id==context.tenant_id,resources.c.kind=='media')).scalar_one()
                    if count+len(event.attachments)>8:
                        raise HTTPException(429,'保存到你的门店后，可以继续添加照片和录音')
                for attachment in event.attachments:
                    raw=await self.transport.download(attachment)
                    result=await process_media(raw,attachment.kind,settings,media_store())
                    if event.audience=='group':
                        from app.tenancy.resources import set_resource_visibility
                        set_resource_visibility('media',result['id'],'store')
                    media_ids.append(result['id'])
                body.update(media_ids=media_ids,media_processed=True)
                save_body('messaging_inbound',row['id'],body)
            turn= get_turn(cid,row['turn_id']) if row.get('turn_id') else enqueue_turn(cid,TurnInput(
                text=event.text,media_ids=media_ids,client_turn_id='im:'+row['id']))
            if turn['status'] in {'queued','running'}:
                # Polling does not consume retry budget; a slow generation is not a failed message.
                self.update(inbox,row,status='awaiting_turn',conversation_id=cid,turn_id=turn['id'],
                    lease_until=time.time()+2,attempts=0)
                return
            text=turn.get('message') or '请接着说这次想做什么。'
            artifact=turn.get('artifact')
            if artifact and artifact.get('kind')=='run':
                # Pure chat users receive the actual saved, reviewed deliverable,
                # rather than only a notice telling them to use the website.
                from .artifacts import artifact_text
                rendered=artifact_text(artifact,event.audience)
                if rendered:
                    text+='\n\n'+rendered
            questions=turn.get('questions',[])
            for question in questions:
                if isinstance(question,str):
                    text+='\n'+question
                elif isinstance(question,dict):
                    text+='\n'+str(question.get('question') or question.get('label') or '')
            self.queue_reply(row,binding,event,text,cid,artifact)

    async def deliver(self,row):
        binding=find_binding(row['binding_id'])
        with as_binding(binding):
            body=load_body('messaging_outbound',row['id'])
            event=InboundEvent.model_validate(body['event'])
            if not group_allowed(binding,event):
                raise HTTPException(403,'群工作空间已更新')
            text=body['text']
            origin=web_origin()
            if origin and body.get('conversation_id') and event.audience=='private':
                code=create_code(event.channel,purpose='handoff',conversation_id=body['conversation_id'],seconds=900)
                text+='\n\n打开网页继续编辑：'+origin+'/#handoff='+code['code']+'&conversation='+body['conversation_id']
            # Recheck session and membership immediately before platform delivery.
            from .identity import binding_context
            binding_context(binding)
            if body.get('artifact') and getattr(self.transport,'supports_images',False):
                from .artifacts import artifact_images
                images=await artifact_images(body['artifact'],event.audience)
                binding_context(binding)
                message_id=await self.transport.send(event,text,row['id'],images=images)
            else:
                message_id=await self.transport.send(event,text,row['id'])
            self.update(outbox,row,status='delivered',message_id=str(message_id or '')[:256],lease_until=0)

    async def tick(self):
        for table,operation in ((inbox,self.process),(outbox,self.deliver)):
            row=await asyncio.to_thread(self.claim,table)
            if not row:
                continue
            try:
                await operation(row)
            except Exception as exc:
                denied=isinstance(exc,HTTPException) and exc.status_code in (401,403,404,410)
                attempts=row['attempts']+1
                terminal=denied or attempts>=5
                values=dict(status='failed' if terminal else 'queued',lease_until=time.time()+min(60,2**attempts))
                if table is outbox:
                    values['next_attempt_at']=values['lease_until']
                self.update(table,row,**values)

    async def run(self):
        while True:
            try:
                await self.tick()
            except Exception:
                # The durable queue survives transient DB/service outages. Do not
                # log SQL, platform URLs, callback content or credentials here.
                log.warning('messaging worker will retry')
                await asyncio.sleep(2)
            await asyncio.sleep(.5)
