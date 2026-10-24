"""Enterprise WeChat official bot SDK transport; callback-driven replies only."""
import asyncio
import logging
import base64
import hashlib
import os
import re
from fastapi import HTTPException
from .gateway import connection_state
from .normalizers import wecom
from .media import download, decrypt_wecom
from .identity import digest

log=logging.getLogger(__name__)


class PlatformReplyError(RuntimeError):
    def __init__(self,operation,code):
        self.operation=operation
        self.code=code
        super().__init__('platform_reply_failed')


class QuietLogger:
    # SDK errors may include full server frames; log only curated state below.
    def debug(self,*args,**kwargs): pass
    def info(self,*args,**kwargs): pass
    def warn(self,*args,**kwargs): pass
    def warning(self,*args,**kwargs): pass
    def error(self,*args,**kwargs): pass
    def exception(self,*args,**kwargs): pass


class WeComTransport:
    supports_images=True
    def __init__(self,config):
        self.config=config
        self.client=None
        self.authenticated=False

    async def download(self,attachment):
        raw=await download(attachment.reference,('work.weixin.qq.com','weixin.qq.com','wework.qpic.cn','qpic.cn'))
        return decrypt_wecom(raw,attachment.aes_key)

    async def checked(self,operation,request):
        try:
            response=await request
        except Exception as exc:
            # SDK errors include req_id/errmsg. Preserve only the numeric code;
            # URLs, platform identifiers and credentials never reach logs.
            match=re.search(r'errcode=(-?\d+)',str(exc))
            code=match[1] if match else ('timeout' if isinstance(exc,TimeoutError) or 'timeout' in str(exc).lower() else 'transport')
            log.warning('wecom operation=%s ack=%s',operation,code)
            raise PlatformReplyError(operation,code) from None
        code=response.get('errcode',0)
        if code!=0:
            safe=str(code) if isinstance(code,int) else 'invalid'
            log.warning('wecom operation=%s ack=%s',operation,safe)
            raise PlatformReplyError(operation,safe)
        log.info('wecom operation=%s ack=0',operation)
        return response

    async def send(self,event,text,identifier,*,images=()):
        if not self.authenticated or not self.client or not self.client.is_connected:
            raise RuntimeError('channel_disconnected')
        # Every delivery is linked to an authenticated inbox callback, never a
        # scheduled campaign or an arbitrary caller-supplied recipient.
        bounded=text.encode('utf-8')[:18000].decode('utf-8',errors='ignore')
        image_mode=os.getenv('MATEQUILL_WECOM_IMAGE_MODE','media')
        items=[{'msgtype':'image','image':{'base64':base64.b64encode(raw).decode('ascii'),
            'md5':hashlib.md5(raw,usedforsecurity=False).hexdigest()}} for raw in images[:10]]
        response=await self.checked('final_text',self.client.reply_stream({'headers':{'req_id':event.response_req_id}},identifier,bounded,
            finish=True,msg_item=(items or None) if image_mode=='mixed' else None))
        if image_mode!='media':
            return response.get('headers',{}).get('req_id',identifier)
        for index,raw in enumerate(images[:10]):
            # Current official Node SDK exposes this same three-step protocol.
            # Text is already visible while the native media transfer completes.
            media_id=await self.upload_image(raw,f'matequill-{identifier[:12]}-{index+1}.jpg')
            await self.checked('image_reply',self.client.reply({'headers':{'req_id':event.response_req_id}},
                {'msgtype':'image','image':{'media_id':media_id}}))
        return response.get('headers',{}).get('req_id',identifier)

    async def upload_image(self,raw,filename):
        from aibot import generate_req_id
        if not raw or len(raw)>10*1024*1024:
            raise ValueError('invalid_artifact_image_size')
        size=512*1024
        async def command(cmd,body):
            response=await self.checked(cmd,self.client.reply({'headers':{'req_id':generate_req_id(cmd)}},body,cmd=cmd))
            return response.get('body',{})
        init=await command('aibot_upload_media_init',{'type':'image','filename':filename,
            'total_size':len(raw),'total_chunks':(len(raw)+size-1)//size,
            'md5':hashlib.md5(raw,usedforsecurity=False).hexdigest()})
        upload_id=init.get('upload_id')
        if not upload_id:
            raise RuntimeError('platform_media_upload_failed')
        # Follow official client.ts uploadChunk(index): indices are zero-based
        # (the SDK type comment currently disagrees with its implementation).
        for index,start in enumerate(range(0,len(raw),size)):
            await command('aibot_upload_media_chunk',{'upload_id':upload_id,'chunk_index':index,
                'base64_data':base64.b64encode(raw[start:start+size]).decode('ascii')})
        result=await command('aibot_upload_media_finish',{'upload_id':upload_id})
        if not result.get('media_id'):
            raise RuntimeError('platform_media_upload_failed')
        return result['media_id']

    async def run(self,gateway):
        from aibot import WSClient, WSClientOptions
        self.client=WSClient(WSClientOptions(bot_id=self.config.app_id,secret=self.config.secret,logger=QuietLogger()))

        @self.client.on('authenticated')
        def authenticated():
            self.authenticated=True
            connection_state(self.config,'connected')
            log.info('wecom authenticated')

        @self.client.on('disconnected')
        def disconnected(_reason):
            self.authenticated=False
            connection_state(self.config,'disconnected')
            log.warning('wecom disconnected')

        @self.client.on('error')
        def error(_error):
            log.warning('wecom transport error')

        @self.client.on('message')
        async def message(frame):
            if not self.authenticated:
                return
            try:
                event=wecom(frame,self.config)
                if event:
                    accepted=await gateway.ingest(event)
                    if accepted:
                        await self.client.reply_stream(frame,digest(event.connection_id,event.event_id),'正在准备这次创作…',finish=False)
            except HTTPException as exc:
                # A user can still bind after their earlier trial/session expired.
                if frame.get('body',{}).get('chattype')!='group':
                    await self.client.reply_stream(frame,'notice-'+digest(frame.get('headers',{}).get('req_id','')),
                        str(exc.detail)[:400],finish=True)
            except Exception:
                # Never echo callback content, user IDs, media URLs or credentials.
                log.warning('wecom callback rejected')

        connection_state(self.config,'connecting')
        await self.client.connect()
        try:
            while True:
                if self.authenticated and self.client.is_connected:
                    connection_state(self.config,'connected')
                await asyncio.sleep(15)
        finally:
            self.authenticated=False
            self.client.disconnect()
            connection_state(self.config,'disconnected')
