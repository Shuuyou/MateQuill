"""Feishu and DingTalk official persistent transports and scoped HTTP media APIs."""
import asyncio
import json
import logging
import time
from urllib.parse import quote,urlparse
import httpx
from .gateway import connection_state
from .media import download,allowed_url
from .normalizers import feishu,dingtalk
from .wecom import QuietLogger


def socket_open(socket):
    return bool(socket and (getattr(socket,'open',False) or getattr(socket,'state',None)==1))


async def post(url,body,headers=None):
    async with httpx.AsyncClient(timeout=20,follow_redirects=False) as client:
        response=await client.post(url,json=body,headers=headers)
        response.raise_for_status()
        if response.status_code!=200 or len(response.content)>1024*1024:
            raise RuntimeError('platform_request_failed')
        return response.json()


class FeishuTransport:
    def __init__(self,config):
        self.config=config
        self.client=None
        self.sdk_loop=None
        self.access_token=''
        self.expires=0

    async def token(self):
        if self.expires<time.time()+60:
            data=await post('https://open.feishu.cn/open-apis/auth/v3/tenant_access_token/internal',
                {'app_id':self.config.app_id,'app_secret':self.config.secret})
            if data.get('code',-1)!=0 or not data.get('tenant_access_token'):
                raise RuntimeError('platform_auth_failed')
            self.access_token=data['tenant_access_token']
            self.expires=time.time()+min(int(data.get('expire',0)),7200)
        return self.access_token

    async def send(self,event,text,identifier):
        # Reply to the original verified message instead of selecting recipients.
        token=await self.token()
        data=await post('https://open.feishu.cn/open-apis/im/v1/messages/'+quote(event.event_id,safe='')+'/reply',
            {'msg_type':'text','content':json.dumps({'text':text[:18000]},ensure_ascii=False),'uuid':identifier[:50]},
            {'Authorization':'Bearer '+token})
        if data.get('code',-1)!=0:
            raise RuntimeError('platform_rejected_reply')
        return data.get('data',{}).get('message_id',identifier)

    async def download(self,attachment):
        token=await self.token()
        url='https://open.feishu.cn/open-apis/im/v1/messages/'+quote(attachment.message_id,safe='')+'/resources/'+quote(attachment.reference,safe='')+'?type='+('image' if attachment.kind=='photo' else 'file')
        return await download(url,('open.feishu.cn',),headers={'Authorization':'Bearer '+token})

    async def run(self,gateway):
        # The official SDK owns its event loop. Keep it in a dedicated thread;
        # callback persistence remains synchronous and bounded before its ACK.
        def sdk_thread():
            self.sdk_loop=asyncio.new_event_loop()
            asyncio.set_event_loop(self.sdk_loop)
            import lark_oapi as lark
            # Imported in this thread, lark.ws binds its documented start loop here.
            import lark_oapi.ws.client as sdk_client
            if sdk_client.loop is not self.sdk_loop:
                raise RuntimeError('Start Feishu in its own worker process')
            logging.getLogger('Lark').disabled=True
            def callback(data):
                event=feishu(json.loads(lark.JSON.marshal(data)),self.config)
                if event:
                    gateway._persist(event)
            handler=lark.EventDispatcherHandler.builder(self.config.encrypt_key,self.config.verification_token).register_p2_im_message_receive_v1(callback).build()
            self.client=lark.ws.Client(self.config.app_id,self.config.secret,event_handler=handler,log_level=lark.LogLevel.ERROR)
            self.client.start()
        connection_state(self.config,'connecting')
        task=asyncio.create_task(asyncio.to_thread(sdk_thread))
        try:
            while not task.done():
                # Pin official SDK version; _conn is transport state, not auth supplied by caller.
                connected=self.client is not None and socket_open(self.client._conn)
                connection_state(self.config,'connected' if connected else 'connecting')
                await asyncio.sleep(10)
            await task
        finally:
            if self.sdk_loop and self.sdk_loop.is_running():
                self.sdk_loop.call_soon_threadsafe(self.sdk_loop.stop)
            connection_state(self.config,'disconnected')


class DingTalkTransport:
    def __init__(self,config):
        self.config=config
        self.client=None
        self.access_token=''
        self.expires=0

    async def token(self):
        if self.expires<time.time()+60:
            data=await post('https://api.dingtalk.com/v1.0/oauth2/accessToken',
                {'appKey':self.config.app_id,'appSecret':self.config.secret})
            if not data.get('accessToken'):
                raise RuntimeError('platform_auth_failed')
            self.access_token=data['accessToken']
            self.expires=time.time()+min(int(data.get('expireIn',0)),7200)
        return self.access_token

    async def send(self,event,text,identifier):
        parsed=urlparse(event.response_url)
        if not allowed_url(event.response_url,('oapi.dingtalk.com',)) or parsed.path!='/robot/sendBySession' or event.response_expires_at<=time.time():
            raise RuntimeError('platform_reply_expired')
        data=await post(event.response_url,{'msgtype':'text','text':{'content':text[:18000]}})
        if data.get('errcode',-1)!=0:
            raise RuntimeError('platform_rejected_reply')
        return identifier

    async def download(self,attachment):
        token=await self.token()
        data=await post('https://api.dingtalk.com/v1.0/robot/messageFiles/download',
            {'robotCode':self.config.app_id,'downloadCode':attachment.reference},
            {'x-acs-dingtalk-access-token':token})
        return await download(data.get('downloadUrl',''),('dingtalk.com','dingding.xin','alicdn.com'))

    async def run(self,gateway):
        import dingtalk_stream as sdk
        config=self.config
        class Client(sdk.DingTalkStreamClient):
            stopping=False
            def open_connection(self):
                # PyPI 0.24.3 predates upstream stop(); its start() explicitly
                # handles KeyboardInterrupt as termination of the reconnect loop.
                if self.stopping:
                    raise KeyboardInterrupt
                return super().open_connection()
        class Handler(sdk.ChatbotHandler):
            async def process(self,callback):
                event=dingtalk(callback.data,config)
                if event:
                    await gateway.ingest(event)
                return sdk.AckMessage.STATUS_OK,'OK'
        self.client=Client(sdk.Credential(config.app_id,config.secret),logger=QuietLogger())
        handler=Handler()
        handler.logger=QuietLogger()
        self.client.register_callback_handler(sdk.ChatbotMessage.TOPIC,handler)
        connection_state(config,'connecting')
        task=asyncio.create_task(self.client.start())
        try:
            while not task.done():
                connection_state(config,'connected' if socket_open(self.client.websocket) else 'connecting')
                await asyncio.sleep(10)
            await task
        finally:
            self.client.stopping=True
            if hasattr(self.client,'stop'):
                await self.client.stop()
            elif self.client.websocket:
                await self.client.websocket.close()
            task.cancel()
            try:
                await asyncio.wait_for(task,12)
            except (asyncio.CancelledError,asyncio.TimeoutError):
                pass
            connection_state(config,'disconnected')
