"""Cookie-authenticated channel controls and explicit one-time browser handoff."""
import json
import time
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import delete, select, update
from app.tenancy import get_context, require_context
from app.tenancy.auth import validate_origin
from app.tenancy.database import db_connection, sessions, tenants, users
from .config import channel_configs, sdk_available
from .identity import code_row, consume_code, create_code
from .tables import bindings, connections

router = APIRouter(prefix='/api/messaging',tags=['messaging'])


class Strict(BaseModel):
    model_config = ConfigDict(extra='forbid')


class BindingCode(Strict):
    channel: str = Field(pattern='^(wecom|feishu|dingtalk|qq)$')
    conversation_id: str | None = Field(default=None,max_length=64)


class Handoff(Strict):
    code: str = Field(min_length=16,max_length=256)
    confirm: bool = False
    switch_workspace: bool = False


@router.get('/connections')
def list_connections():
    require_context()
    with db_connection() as connection:
        states = {r['id']:r for r in connection.execute(select(connections)).mappings()}
    result=[]
    for config in channel_configs().values():
        row=states.get(config.connection_id,{})
        connected = config.configured and row.get('status')=='connected' and row.get('heartbeat_at',0)>time.time()-45
        # Only installed, implemented transports can offer a binding flow.
        installed=sdk_available(config)
        enabled=config.channel in {'wecom','feishu','dingtalk'}
        status='connected' if connected else ('not_enabled' if not enabled else ('unconfigured' if not config.configured else ('ready' if installed else 'dependency_missing')))
        result.append(dict(channel=config.channel,label=config.label,configured=config.configured,
            connected=bool(connected),status=status,capabilities=dict(text=True,photo=True,audio=True,group=False)))
    result.append(dict(channel='wechat',label='微信',configured=False,connected=False,status='not_enabled',capabilities=dict(text=False,photo=False,audio=False,group=False)))
    return {'items':result}


@router.post('/bindings/code')
def binding_code(payload:BindingCode):
    require_context()
    config=channel_configs()[payload.channel]
    if config.channel not in {'wecom','feishu','dingtalk'} or not config.configured or not sdk_available(config):
        raise HTTPException(409,'此入口尚未连接，请先使用网页创作')
    return {**create_code(payload.channel,conversation_id=payload.conversation_id),
        'instruction':'在机器人私聊中发送：绑定 <连接码>，即可接着这次创作。'}


@router.get('/bindings')
def list_bindings():
    context=require_context()
    with db_connection() as connection:
        rows=connection.execute(select(bindings).where(bindings.c.user_id==context.user_id,
            bindings.c.tenant_id==context.tenant_id,bindings.c.store_id==context.store_id,bindings.c.active.is_(True))).mappings().all()
    return {'items':[{key:r[key] for key in ('id','channel','created_at','last_seen_at','active','store_id')} for r in rows]}


@router.delete('/bindings/{identifier}')
def revoke_binding(identifier:str):
    context=require_context()
    with db_connection() as connection:
        row=connection.execute(select(bindings).where(bindings.c.id==identifier,
            bindings.c.user_id==context.user_id,bindings.c.tenant_id==context.tenant_id,
            bindings.c.store_id==context.store_id)).mappings().first()
        if not row:
            raise HTTPException(404,'未找到这个入口')
        connection.execute(update(bindings).where(bindings.c.id==identifier).values(active=False))
        connection.execute(delete(sessions).where(sessions.c.token_hash==row['session_hash']))
    return {'ok':True}


@router.post('/handoff/exchange')
def exchange(payload:Handoff,request:Request):
    validate_origin(request)
    row,target=code_row(payload.code,'handoff')
    current=get_context()
    switching=bool(current and (current.user_id,current.tenant_id,current.store_id)!=(target.user_id,target.tenant_id,target.store_id))
    if switching and payload.confirm and not payload.switch_workspace:
        raise HTTPException(409,'请确认切换到企微会话后继续')
    with db_connection() as connection:
        user=connection.execute(select(users.c.name).where(users.c.id==target.user_id)).scalar_one()
        workspace=connection.execute(select(tenants.c.name).where(tenants.c.id==target.tenant_id)).scalar_one()
    if not payload.confirm:
        return {'requires_confirmation':True,'requires_workspace_switch':switching,'user_name':user,'workspace_name':workspace,
            'conversation_id':row['conversation_id'],'expires_at':row['expires_at']}
    from app.tenant_routes import _response_with_session
    from app.tenancy.auth import guest_state
    from app.trial_onboarding import TRIAL_SECONDS
    with db_connection() as connection:
        consume_code(connection,row)
    seconds=TRIAL_SECONDS if guest_state(target.user_id,target.tenant_id)['is_guest'] else 7*86400
    response=_response_with_session(request,target.user_id,target.tenant_id,target.store_id,seconds=seconds)
    body=json.loads(response.body)
    body['conversation_id']=row['conversation_id']
    response.body=json.dumps(body,ensure_ascii=False).encode()
    response.headers['content-length']=str(len(response.body))
    return response
