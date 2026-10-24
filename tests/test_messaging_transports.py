"""Provider protocol fixtures; no live message is sent."""
import asyncio
import time
import pytest
from app.messaging.config import ChannelConfig
from app.messaging.models import InboundEvent,Attachment
from app.messaging.enterprise import DingTalkTransport,FeishuTransport
from app.messaging.wecom import WeComTransport


def test_worker_requires_explicit_shared_database(monkeypatch):
    from scripts.run_messaging import run
    monkeypatch.delenv('MATEQUILL_DATABASE_URL',raising=False)
    with pytest.raises(SystemExit,match='same database as the web backend'):
        asyncio.run(run('wecom'))


def test_worker_invalid_database_error_never_echoes_credentials(monkeypatch):
    from scripts.run_messaging import run
    monkeypatch.setenv('MATEQUILL_DATABASE_URL','unsupported://private-user:private-password@localhost/db')
    with pytest.raises(SystemExit) as exc:
        asyncio.run(run('wecom'))
    assert 'private-password' not in str(exc.value)


def message(channel,config,**kwargs):
    return InboundEvent(channel=channel,connection_id=config.connection_id,event_id='message-original',
        sender_id='sender',chat_id='chat',text='内容',**kwargs)


def test_feishu_uses_reply_api_with_idempotency(monkeypatch):
    from app.messaging import enterprise
    config=ChannelConfig('feishu','飞书','app','secret','lark_oapi')
    transport=FeishuTransport(config)
    transport.access_token='test-token'
    transport.expires=time.time()+500
    calls=[]
    async def fake(url,body,headers=None):
        calls.append((url,body,headers))
        return {'code':0,'data':{'message_id':'sent-id'}}
    monkeypatch.setattr(enterprise,'post',fake)
    assert asyncio.run(transport.send(message('feishu',config),'作品','outbox-key'))=='sent-id'
    assert calls[0][0].endswith('/message-original/reply')
    assert calls[0][1]['uuid']=='outbox-key'


def test_dingtalk_rejects_arbitrary_and_expired_reply_urls(monkeypatch):
    config=ChannelConfig('dingtalk','钉钉','app','secret','dingtalk_stream')
    transport=DingTalkTransport(config)
    for url,expires in [('https://attacker.test/robot/sendBySession',time.time()+100),
        ('https://oapi.dingtalk.com/robot/sendBySession?session=x',time.time()-1),
        ('https://oapi.dingtalk.com/arbitrary',time.time()+100)]:
        with pytest.raises(RuntimeError):
            asyncio.run(transport.send(message('dingtalk',config,response_url=url,response_expires_at=expires),'内容','id'))


def test_wecom_preserves_callback_reply_scope_and_checks_ack():
    config=ChannelConfig('wecom','企业微信','app','secret','aibot')
    transport=WeComTransport(config)
    calls=[]
    class SDK:
        is_connected=True
        async def reply_stream(self,*args,**kwargs):
            calls.append((args,kwargs))
            return {'errcode':0,'headers':{'req_id':'ack'}}
    transport.client=SDK()
    transport.authenticated=True
    assert asyncio.run(transport.send(message('wecom',config,response_req_id='callback-id'),'结果','outbox-key'))=='ack'
    assert calls[0][0][0]['headers']['req_id']=='callback-id'
    assert calls[0][1]['finish'] is True
    transport.authenticated=False
    with pytest.raises(RuntimeError):
        asyncio.run(transport.send(message('wecom',config),'结果','outbox-key'))


def test_wecom_final_stream_embeds_exact_image_bytes(monkeypatch):
    monkeypatch.setenv('MATEQUILL_WECOM_IMAGE_MODE','mixed')
    import base64,hashlib
    config=ChannelConfig('wecom','企业微信','app','secret','aibot')
    transport=WeComTransport(config)
    calls=[]
    class SDK:
        is_connected=True
        async def reply_stream(self,*args,**kwargs):
            calls.append(kwargs)
            return {'errcode':0}
    transport.client=SDK()
    transport.authenticated=True
    raw=b'actual-verified-artifact-image'
    asyncio.run(transport.send(message('wecom',config),'图文','same-stream',images=[raw]))
    image=calls[0]['msg_item'][0]['image']
    assert base64.b64decode(image['base64'])==raw
    assert image['md5']==hashlib.md5(raw,usedforsecurity=False).hexdigest()
    assert calls[0]['finish'] is True


def test_wecom_native_image_upload_and_reply_use_official_protocol():
    import base64
    config=ChannelConfig('wecom','企业微信','app','secret','aibot')
    transport=WeComTransport(config)
    calls=[]
    class SDK:
        is_connected=True
        async def reply(self,frame,body,cmd=None):
            calls.append((cmd,body,frame))
            return {'errcode':0,'body':{'upload_id':'upload','media_id':'media'}}
        async def reply_stream(self,*args,**kwargs):
            return {'errcode':0}
    transport.client=SDK()
    transport.authenticated=True
    raw=b'actual-image-bytes'
    asyncio.run(transport.send(message('wecom',config,response_req_id='original-callback'),'图文','stream',images=[raw]))
    assert [c[0] for c in calls]==['aibot_upload_media_init','aibot_upload_media_chunk','aibot_upload_media_finish',None]
    assert base64.b64decode(calls[1][1]['base64_data'])==raw
    assert calls[1][1]['chunk_index']==0
    assert calls[-1][1]=={'msgtype':'image','image':{'media_id':'media'}}
    assert calls[-1][2]['headers']['req_id']=='original-callback'


def test_wecom_native_multichunk_uses_zero_based_indices():
    import base64
    config=ChannelConfig('wecom','企业微信','app','secret','aibot')
    transport=WeComTransport(config)
    chunks=[]
    class SDK:
        async def reply(self,frame,body,cmd=None):
            if cmd=='aibot_upload_media_chunk': chunks.append(body)
            return {'errcode':0,'body':{'upload_id':'upload','media_id':'media'}}
    transport.client=SDK()
    raw=b'a'*(512*1024)+b'final-chunk'
    assert asyncio.run(transport.upload_image(raw,'image.jpg'))=='media'
    assert [part['chunk_index'] for part in chunks]==[0,1]
    assert b''.join(base64.b64decode(part['base64_data']) for part in chunks)==raw


def test_platform_diagnostics_keep_only_numeric_ack(caplog):
    from app.messaging.wecom import PlatformReplyError
    config=ChannelConfig('wecom','企业微信','app','secret','aibot')
    transport=WeComTransport(config)
    async def rejected():
        raise RuntimeError('Reply ack error: errcode=40058, errmsg=signed-url-secret-user-id')
    with pytest.raises(PlatformReplyError) as exc:
        asyncio.run(transport.checked('aibot_upload_media_chunk',rejected()))
    assert exc.value.code=='40058'
    assert '40058' in caplog.text
    assert 'signed-url-secret-user-id' not in caplog.text
