"""Real tenancy boundaries around official messaging callbacks, without sending messages."""
import asyncio
import base64
from contextlib import contextmanager
from uuid import uuid4
import pytest
from fastapi import HTTPException
from sqlalchemy import select,update
from app.messaging.config import ChannelConfig
from app.messaging.models import InboundEvent
from app.messaging.normalizers import wecom,feishu,dingtalk
from app.messaging.media import allowed_url,decrypt_wecom
from app.messaging.identity import create_code,code_row,bind_code,resolve_identity,as_binding
from app.messaging.gateway import Gateway
from app.messaging.tables import inbox,outbox,bindings
from app.tenancy import set_context,reset_context,load_body,save_body
from app.tenancy.auth import new_session,session_row,context_from_session
from app.tenancy.database import clear_engine_cache,db_connection
from app.store_campaigns import StoreRepository,StoreInput
from scripts.tenant_admin import bootstrap

pytestmark=pytest.mark.real_tenancy
CONFIG=ChannelConfig('wecom','企业微信','test-bot','test-secret','aibot')


@pytest.fixture
def owner(tmp_path,monkeypatch):
    monkeypatch.setenv('MATEQUILL_DATABASE_URL','sqlite:///'+(tmp_path/'messaging.sqlite').as_posix())
    clear_engine_cache()
    account=bootstrap(email=uuid4().hex+'@example.test',name='测试店长',tenant_name='门店测试',password='Messaging-Tests-Only-2026!')
    context=context_from_session(session_row(new_session(account['user_id'],account['tenant_id'])))
    token=set_context(context)
    try:
        store=StoreRepository().save_store(StoreInput(name='门店',audience='附近顾客'))
    finally:
        reset_context(token)
    token=set_context(context_from_session(session_row(new_session(account['user_id'],account['tenant_id'],store['id']))))
    yield
    reset_context(token)
    clear_engine_cache()


def event(identifier='event-1',**kwargs):
    return InboundEvent(channel='wecom',connection_id=CONFIG.connection_id,event_id=identifier,
        sender_id='platform-user',chat_id='platform-user',text='写一篇门店新品推广',**kwargs)


class Transport:
    config=CONFIG
    def __init__(self): self.sent=[]
    async def send(self,message,text,identifier):
        self.sent.append((message,text,identifier))
        return 'ack-message'


def test_binding_one_time_scope(owner):
    code=create_code('wecom')
    binding=bind_code(event(),code['code'])
    with as_binding(binding):
        assert binding['store_id']
    with pytest.raises(HTTPException) as exc:
        bind_code(event(),code['code'])
    assert exc.value.status_code==410
    with pytest.raises(HTTPException):
        code_row(code['code'],'handoff')


def test_duplicate_inbox_and_untrusted_group(owner):
    bind_code(event(),create_code('wecom')['code'])
    gateway=Gateway(Transport())
    assert asyncio.run(gateway.ingest(event()))
    assert not asyncio.run(gateway.ingest(event()))
    assert not asyncio.run(gateway.ingest(event('group-event',audience='group')))
    with db_connection() as connection:
        assert len(connection.execute(select(inbox)).all())==1


def test_outbox_requires_actual_ack_and_rechecks_binding(owner,monkeypatch):
    binding=bind_code(event(),create_code('wecom')['code'])
    transport=Transport()
    gateway=Gateway(transport)
    asyncio.run(gateway.ingest(event()))
    row=gateway.claim(inbox)
    with as_binding(binding):
        gateway.queue_reply(row,binding,event(),'作品已准备好')
    row=gateway.claim(outbox)
    with db_connection() as connection:
        connection.execute(update(bindings).where(bindings.c.id==binding['id']).values(active=False))
    with pytest.raises(HTTPException):
        asyncio.run(gateway.deliver(row))
    assert transport.sent==[]


def test_shared_agent_turn_to_real_outbox_ack(owner,monkeypatch):
    from app.gtm_agent import service
    calls=[]
    def enqueue(cid,payload):
        calls.append((cid,payload))
        return {'id':'turn-result','status':'completed','message':'今天的门店新品文案','questions':[]}
    monkeypatch.setattr(service,'enqueue_turn',enqueue)
    binding=bind_code(event(),create_code('wecom')['code'])
    transport=Transport()
    gateway=Gateway(transport)
    asyncio.run(gateway.ingest(event()))
    asyncio.run(gateway.tick())
    assert len(calls)==1
    assert calls[0][1].client_turn_id.startswith('im:')
    assert transport.sent[0][1]=='今天的门店新品文案'
    with db_connection() as connection:
        assert connection.execute(select(outbox.c.status)).scalar_one()=='delivered'


def test_chat_delivers_saved_reviewed_artifact_body(owner,monkeypatch):
    from app.gtm_agent import service
    save_body('run','approved-artifact',{'status':'ready','title':'新品一起体验','intro':'周末来门店看看',
        'blocks':[{'text':'这是已保存稿件的正文。'}],'cta':'来店聊聊你的需求','tags':['门店日常']})
    monkeypatch.setattr(service,'enqueue_turn',lambda cid,payload:{'id':'turn','status':'completed',
        'message':'准备好了','questions':[],'artifact':{'kind':'run','id':'approved-artifact'}})
    bind_code(event(),create_code('wecom')['code'])
    transport=Transport()
    gateway=Gateway(transport)
    asyncio.run(gateway.ingest(event()))
    asyncio.run(gateway.tick())
    assert '这是已保存稿件的正文。' in transport.sent[0][1]
    assert '#门店日常' in transport.sent[0][1]


def test_refresh_link_uses_existing_conversation_without_llm(owner,monkeypatch):
    from app.gtm_agent import service
    from app.messaging import gateway as gateway_module
    from app.messaging.tables import codes,threads
    import time
    monkeypatch.setattr(service,'enqueue_turn',lambda *args:pytest.fail('A link refresh must not call the model'))
    monkeypatch.setattr(gateway_module,'web_origin',lambda:'https://workspace.example.test')
    bind_code(event(),create_code('wecom')['code'])
    transport=Transport()
    gateway=Gateway(transport)
    incoming=event().model_copy(update={'text':'打开网页'})
    asyncio.run(gateway.ingest(incoming))
    asyncio.run(gateway.tick())
    assert '#handoff=' in transport.sent[0][1]
    with db_connection() as connection:
        code=connection.execute(select(codes).where(codes.c.purpose=='handoff')).mappings().one()
        cid=connection.execute(select(threads.c.conversation_id)).scalar_one()
    assert code['conversation_id']==cid
    assert 890<code['expires_at']-time.time()<=900


def test_continue_editing_includes_current_saved_artifact_without_model(owner,monkeypatch):
    from app.gtm_agent import service
    from app.gtm_agent.repository import create_conversation,link_artifact
    from app.messaging.tables import threads
    from app.messaging.identity import thread_key
    from app.messaging import artifacts
    import time
    save_body('run','current-copy',{'status':'ready','title':'当前作品','blocks':[{'text':'继续编辑时应出现的正文'}]})
    conversation=create_conversation()
    link_artifact(conversation['id'],'run','current-copy')
    binding=bind_code(event(),create_code('wecom',conversation_id=conversation['id'])['code'])
    transport=Transport()
    gateway=Gateway(transport)
    monkeypatch.setattr(service,'enqueue_turn',lambda *args:pytest.fail('Must not regenerate to retrieve a saved artifact'))
    incoming=event().model_copy(update={'text':'继续编辑'})
    asyncio.run(gateway.ingest(incoming))
    asyncio.run(gateway.tick())
    assert '继续编辑时应出现的正文' in transport.sent[0][1]


def test_outbox_failed_send_is_retryable_not_delivered(owner):
    binding=bind_code(event(),create_code('wecom')['code'])
    class Broken(Transport):
        async def send(self,*args): raise TimeoutError('network')
    gateway=Gateway(Broken())
    asyncio.run(gateway.ingest(event()))
    row=gateway.claim(inbox)
    with as_binding(binding): gateway.queue_reply(row,binding,event(),'待发送内容')
    asyncio.run(gateway.tick())
    with db_connection() as connection:
        record=connection.execute(select(outbox)).mappings().one()
    assert record['status']=='queued'
    assert record['message_id'] is None
    assert record['attempts']==1


def test_binding_replacement_does_not_inherit_thread(owner):
    first=bind_code(event(),create_code('wecom')['code'])
    second=bind_code(event(),create_code('wecom')['code'])
    assert first['id']!=second['id']
    with pytest.raises(HTTPException):
        with as_binding(first): pass


def test_handoff_preview_confirm_and_single_use(owner):
    from starlette.requests import Request
    from app.messaging.routes import exchange,Handoff
    from app.gtm_agent.service import create_conversation
    code=create_code('wecom',purpose='handoff',conversation_id=create_conversation()['id'],seconds=90)
    request=Request({'type':'http','method':'POST','path':'/api/messaging/handoff/exchange','scheme':'http',
        'server':('127.0.0.1',3003),'headers':[(b'origin',b'http://127.0.0.1:3003')],'query_string':b''})
    preview=exchange(Handoff(code=code['code']),request)
    assert preview['requires_confirmation']
    assert code_row(code['code'],'handoff')
    response=exchange(Handoff(code=code['code'],confirm=True),request)
    assert 'httponly' in response.headers['set-cookie'].lower()
    with pytest.raises(HTTPException):
        code_row(code['code'],'handoff')


def test_invalid_handoff_origin_is_rejected(owner):
    from starlette.requests import Request
    from app.messaging.routes import exchange,Handoff
    code=create_code('wecom',purpose='handoff',seconds=90)
    request=Request({'type':'http','method':'POST','path':'/','scheme':'http','server':('localhost',3003),
        'headers':[(b'origin',b'https://evil.example')],'query_string':b''})
    with pytest.raises(HTTPException) as exc:
        exchange(Handoff(code=code['code'],confirm=True),request)
    assert exc.value.status_code==403


def test_handoff_never_silently_switches_existing_browser(owner):
    from starlette.requests import Request
    from app.messaging.routes import exchange,Handoff
    from app.tenancy import TenantContext
    code=create_code('wecom',purpose='handoff',seconds=90)
    request=Request({'type':'http','method':'POST','path':'/','scheme':'http','server':('localhost',3003),
        'headers':[(b'origin',b'http://localhost:3003')],'query_string':b''})
    token=set_context(TenantContext(user_id='another-user',tenant_id='another-tenant',store_id='another-store'))
    try:
        preview=exchange(Handoff(code=code['code']),request)
        assert preview['requires_workspace_switch'] is True
        with pytest.raises(HTTPException) as exc:
            exchange(Handoff(code=code['code'],confirm=True),request)
        assert exc.value.status_code==409
        assert code_row(code['code'],'handoff')
        response=exchange(Handoff(code=code['code'],confirm=True,switch_workspace=True),request)
        assert 'httponly' in response.headers['set-cookie'].lower()
    finally:
        reset_context(token)
    with pytest.raises(HTTPException):
        code_row(code['code'],'handoff')


def test_expired_lease_owner_cannot_finalize_after_reclaim(owner):
    binding=bind_code(event(),create_code('wecom')['code'])
    gateway=Gateway(Transport())
    asyncio.run(gateway.ingest(event()))
    first=gateway.claim(inbox)
    assert gateway.claim(inbox) is None
    with db_connection() as connection:
        connection.execute(update(inbox).where(inbox.c.id==first['id']).values(lease_until=0))
    second=gateway.claim(inbox)
    assert second['claim_token']!=first['claim_token']
    with as_binding(binding):
        with pytest.raises(RuntimeError,match='message_lease_lost'):
            gateway.queue_reply(first,binding,event(),'旧worker的回复')
    with db_connection() as connection:
        assert not connection.execute(select(outbox)).first()


def test_platform_normalization_and_group_mentions():
    frame={'cmd':'aibot_msg_callback','headers':{'req_id':'req'},'body':{'aibotid':'test-bot','msgid':'1',
        'from':{'userid':'sender'},'chattype':'single','msgtype':'voice','voice':{'content':'帮我发新品'}}}
    assert wecom(frame,CONFIG).text=='帮我发新品'
    frame['body']['aibotid']='another-bot'
    assert wecom(frame,CONFIG) is None
    assert dingtalk({'conversationType':'2','isInAtList':False},CONFIG) is None
    assert feishu({'event':{'sender':{'sender_id':{'open_id':'user'}},'message':{'message_id':'id','chat_type':'group'}}},CONFIG) is None


def test_media_hosts_and_real_encryption():
    assert allowed_url('https://wework.qpic.cn/file',('qpic.cn',))
    for url in ('http://wework.qpic.cn/file','https://qpic.cn.evil.test/file','https://127.0.0.1/file','https://user@qpic.cn/file','https://qpic.cn:444/file'):
        assert not allowed_url(url,('qpic.cn',))
    from cryptography.hazmat.primitives.ciphers import Cipher,algorithms,modes
    key=bytes(range(32))
    data=b'photo-payload'
    padding=32-len(data)%32
    encryptor=Cipher(algorithms.AES(key),modes.CBC(key[:16])).encryptor()
    cipher=encryptor.update(data+bytes([padding])*padding)+encryptor.finalize()
    assert decrypt_wecom(cipher,base64.b64encode(key).decode())==data
    with pytest.raises(ValueError):
        decrypt_wecom(cipher[:-1],base64.b64encode(key).decode())
