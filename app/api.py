"""HTTP boundary: local-only credential setup and evidence-gated exports."""
import asyncio
import html
import ipaddress
import json
import time
from collections import OrderedDict, defaultdict, deque
from pathlib import Path
from urllib.parse import urlparse

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, ConfigDict, Field

from .config import ROOT, settings
from .ima import ImaProvider, ImaError
from .models import TaskInput

app = FastAPI(title='场景有据 · 华为 PC 营销智能体', docs_url=None, redoc_url=None)
ima = ImaProvider(settings)
runs = OrderedDict()
semaphore = asyncio.Semaphore(2)
requests_by_client = defaultdict(deque)
demo_provider = None
local_provider = None


def local_settings_allowed(request):
    if not settings.allow_local_settings:
        return False
    try:
        peer = request.client.host if request.client else ''
        hostname = request.url.hostname or ''
        if peer == 'testclient':
            return False
        if not ipaddress.ip_address(peer).is_loopback:
            return False
        if hostname != 'localhost' and not ipaddress.ip_address(hostname).is_loopback:
            return False
    except ValueError:
        return False
    origin = request.headers.get('origin')
    if origin:
        parsed = urlparse(origin)
        if parsed.scheme not in ('http', 'https') or parsed.netloc != request.headers.get('host'):
            return False
    return True


def get_demo():
    global demo_provider
    if demo_provider is None:
        from .demo import DemoProvider
        demo_provider = DemoProvider()
    return demo_provider


def get_local():
    global local_provider
    if local_provider is None:
        from .local_knowledge import LocalKnowledgeProvider
        local_provider = LocalKnowledgeProvider()
    return local_provider


class SettingsInput(BaseModel):
    model_config = ConfigDict(extra='forbid')
    ima_client_id: str | None = Field(default=None,max_length=512)
    ima_api_key: str | None = Field(default=None,max_length=2048)
    ima_kb_id: str | None = Field(default=None,max_length=2048)
    llm_base_url: str | None = Field(default=None,max_length=512)
    llm_api_key: str | None = Field(default=None,max_length=2048)
    llm_model: str | None = Field(default=None,max_length=200)
    image_base_url: str | None = Field(default=None,max_length=512)
    image_api_key: str | None = Field(default=None,max_length=2048)
    image_model: str | None = Field(default=None,max_length=200)
    image_review_model: str | None = Field(default=None,max_length=200)


@app.get('/api/status')
async def status(request: Request):
    return {'ok':True, 'settings':settings.public(),
            'modes':{'local':True,'demo':True,'live':settings.ima_ready and bool(settings.llm_api_key)},
            'ima':{'connected':bool(ima.last_probe and ima.last_probe.get('ok')),'last_probe':ima.last_probe},
            'local_settings_allowed':local_settings_allowed(request)}


@app.post('/api/settings')
async def update_settings(payload: SettingsInput, request: Request):
    global ima
    if not local_settings_allowed(request):
        raise HTTPException(403,'设置凭证仅允许本机 loopback 同源访问；线上请使用服务端 Secrets')
    values = payload.model_dump(exclude_none=True)
    for field in ('llm_base_url','image_base_url'):
        if field not in values:
            continue
        parsed = urlparse(values[field])
        if parsed.scheme != 'https' or parsed.username or parsed.password or not parsed.hostname or parsed.query or parsed.fragment:
            raise HTTPException(422,'模型接口必须为不含凭证的 HTTPS URL')
    for key, value in values.items():
        setattr(settings,key,value.strip())
    ima = ImaProvider(settings)
    return {'ok':True,'settings':settings.public()}


@app.post('/api/probe')
async def probe():
    try:
        result = await ima.probe()
        return {'ok':True,'ima':result,'llm':{'configured':bool(settings.llm_api_key),
                                             'model':settings.llm_model,'tested':False}}
    except ImaError as exc:
        return JSONResponse({'ok':False,'ima':{'ok':False,'error':str(exc)},
                             'llm':{'configured':bool(settings.llm_api_key),'tested':False}},status_code=400)


@app.get('/api/catalog')
async def catalog(mode: str = 'local'):
    if mode not in ('local','demo','live'):
        raise HTTPException(422,'mode 必须是 local、demo 或 live')
    try:
        provider = get_demo() if mode == 'demo' else get_local() if mode == 'local' else ima
        items = provider.catalog()
        if hasattr(items,'__await__'):
            items = await items
        return {'items':items,'mode':mode}
    except ImaError as exc:
        raise HTTPException(400,str(exc)) from None


class NaturalIntakeInput(BaseModel):
    query: str = Field(min_length=1, max_length=3000)
    mode: str = Field(default='local', pattern=r'^(local|live|demo)$')
    answers: dict[str, str] = Field(default_factory=dict)


@app.post('/api/intake')
async def natural_intake(payload: NaturalIntakeInput, request: Request):
    from .intake import resolve_intake
    from .llm import OpenAICompatibleLLM
    key = 'intake:' + (request.client.host if request.client else 'unknown')
    now = time.monotonic()
    history = requests_by_client[key]
    while history and now - history[0] > 60:
        history.popleft()
    if len(history) >= 12:
        raise HTTPException(429, '请稍等片刻再继续。')
    history.append(now)
    answers = {key: value[:1500] for key, value in list(payload.answers.items())[:12]}
    provider = get_demo() if payload.mode == 'demo' else get_local() if payload.mode == 'local' else ima
    try:
        catalog_items = provider.catalog()
        if hasattr(catalog_items, '__await__'):
            catalog_items = await asyncio.wait_for(catalog_items, timeout=15)
    except ImaError as exc:
        raise HTTPException(400, str(exc)) from None
    llm = OpenAICompatibleLLM(settings.llm_base_url, settings.llm_api_key, settings.llm_model,
                             timeout=20, max_tokens=1400, max_calls=1)
    async with semaphore:
        try:
            return await asyncio.wait_for(resolve_intake(payload.query, catalog_items,
                llm if llm.enabled else None, answers), timeout=25)
        except (asyncio.TimeoutError, ValueError):
            return await resolve_intake(payload.query, catalog_items, None, answers)


@app.post('/api/run')
async def run_task(task: TaskInput, request: Request):
    from .workflow import AgentWorkflow
    from .llm import OpenAICompatibleLLM
    from .creative_connectors import get_connector
    from .models import EvidenceBundle
    try:
        connector=get_connector(task.platform,task.creative_connector)
        connector.build_brief(task,EvidenceBundle(product=task.product),{})
    except (KeyError,ValueError):
        raise HTTPException(422,'创意连接器或风格与所选平台不匹配') from None
    key = request.client.host if request.client else 'unknown'
    now = time.monotonic()
    history = requests_by_client[key]
    while history and now-history[0] > 60:
        history.popleft()
    if len(history) >= 10:
        raise HTTPException(429,'请求较多，请稍后再试')
    history.append(now)
    if task.mode == 'live' and not (settings.ima_ready and settings.llm_api_key):
        raise HTTPException(400,'真实模式需要同时配置 ima 和模型服务')
    if task.mode == 'local' and not settings.llm_api_key:
        raise HTTPException(400,'本地正式模式仅需配置模型服务，无需 ima 凭证')
    provider = get_demo() if task.mode == 'demo' else get_local() if task.mode == 'local' else ima
    if task.ecosystem_products or task.media_ids:
        from .task_context import TaskContextProvider
        provider = TaskContextProvider(provider, task)
    llm = OpenAICompatibleLLM(settings.llm_base_url,settings.llm_api_key,settings.llm_model)
    try:
        async with semaphore:
            result = await asyncio.wait_for(AgentWorkflow(provider,llm).run(task),timeout=150)
    except asyncio.TimeoutError:
        raise HTTPException(504,'生成超时；未放行未经核验的内容') from None
    except ImaError as exc:
        raise HTTPException(400,str(exc)) from None
    except Exception:
        raise HTTPException(500,'本次任务未完成，请检查接口状态后重试；未放行未经核验的内容') from None
    record = result.model_dump(mode='json')
    record['_poster_task']=task.model_dump()
    for item in record.get('assets',[]):
        item.pop('local_path',None)
        # Frontend URLs are always opaque application asset routes.
        item['url'] = '/api/assets/' + item['id']
    for source in record.get('sources',[]):
        # Sources use document titles + public hashes. Never return signed URLs.
        from .local_knowledge import official_url
        if source.get('provider')!='huawei_official' or not official_url(source.get('url','')):
            source['url'] = ''
        quotes=[fact.get('quote','') for fact in record.get('facts',[])
                if fact.get('source_id')==source.get('id') and fact.get('quote')]
        source['excerpt']='\n\n'.join(dict.fromkeys(quotes))[:8000] if quotes else '仅展示本次引用的资料名称，不公开资料全文。'
    run_id = record.get('id') or record.get('run_id')
    if run_id:
        runs[run_id] = record
        from .run_records import save_run
        save_run(run_id, record)
        while len(runs)>100:
            runs.popitem(last=False)
    return JSONResponse(record)


@app.get('/api/runs/{run_id}')
async def read_run(run_id: str):
    from .run_records import load_run
    record = runs.get(run_id) or load_run(run_id)
    if record is None:
        raise HTTPException(404, '没有找到这次创作，可以用保留的需求重新开始')
    return JSONResponse(record, headers={'Cache-Control':'private, no-store'})


@app.get('/api/assets/{asset_id}')
async def asset(asset_id: str):
    if not asset_id or len(asset_id)>120 or '/' in asset_id or '..' in asset_id:
        raise HTTPException(404,'素材不存在')
    try:
        if asset_id.startswith('upload-'):
            from .media_routes import media_store
            record = media_store().get(asset_id.removeprefix('upload-'))
            if record and record[1].startswith('image/'):
                return FileResponse(record[0], media_type=record[1], headers={
                    'Cache-Control':'private, no-store', 'X-Content-Type-Options':'nosniff'})
        if asset_id in ima.assets:
            data,mime = await ima.get_asset(asset_id)
            return Response(data,media_type=mime,headers={'Cache-Control':'private, max-age=120',
                            'X-Content-Type-Options':'nosniff'})
        for demo in (get_demo(),get_local()):
            if not hasattr(demo,'get_asset'):
                continue
            value = demo.get_asset(asset_id)
            if hasattr(value,'__await__'):
                value = await value
            if isinstance(value,tuple):
                return Response(value[0],media_type=value[1],headers={'Cache-Control':'private, max-age=120'})
            if value:
                roots = [(ROOT/'research'/'knowledge').resolve(),(ROOT/'runtime'/'official-assets').resolve()]
                target = Path(value).resolve()
                if any(target.is_relative_to(root) for root in roots) and target.suffix.lower() in ('.png','.jpg','.jpeg','.webp'):
                    return FileResponse(target)
    except (ImaError,KeyError,ValueError):
        pass
    raise HTTPException(404,'素材不存在或会话已过期')


def markdown_export(record):
    watermark = '【演示/待核验草稿，不可直接发布】\n\n' if record.get('status')!='ready' else ''
    strategy=record.get('strategy') or {}
    text = '一、场景判断\n'+'\n'.join(f'- {key}：{value}' for key,value in strategy.items())
    text += '\n\n二、知识库依据\n'+'\n'.join('- '+s.get('title','') for s in record.get('sources',[]))
    text += '\n\n三、图文软文\n\n'+'\n\n'.join(filter(None, [record.get('title',''),record.get('intro',''),
        *[block.get('text','')+('' if not block.get('disclosures') else '\n\n适用说明：'+'；'.join(block['disclosures'])) for block in record.get('blocks',[])],record.get('cta','')]))
    if record.get('images'):
        lookup = {a['id']:a for a in record.get('assets',[])}
        placements = []
        for image in record['images']:
            item = lookup.get(image.get('asset_id'),{})
            placements.append(f"- {image.get('position','')}：{image.get('caption','')}（来源：{item.get('title','待确认')}）")
        text += '\n\n配图安排\n' + '\n'.join(placements)
    text += '\n\n四、核验结果\n'+'\n'.join('- '+a.get('message','') for a in record.get('audit',[]))
    if record.get('editorial_review'):
        text+='\n\n内容编辑建议\n'+'\n'.join('- '+a['message'] for a in record['editorial_review'])
    creative=record.get('creative_plan') or {}
    if creative.get('cards'):
        text+='\n\n五、图文分镜\n连接器：'+creative.get('connector_id','')+' / '+creative.get('connector_version','')
        for index,card in enumerate(creative['cards'],1):
            text+='\n\n第'+str(index)+'页：'+card.get('headline','')+'\n'+card.get('body','')
            if card.get('disclosures'):
                text+='\n适用说明：'+'；'.join(card['disclosures'])
    if record.get('sales_plan'):
        plan=record['sales_plan']
        text+='\n\n附录：销售方案（建议，未对外执行）\n'+plan.get('next_action','')
        text+='\n\n演示脚本\n'+'\n'.join('- '+x.get('time','')+' '+x.get('action','') for x in plan.get('demo_script',[]))
        text+='\n\n跟进计划\n'+plan.get('followup_anchor','')+'\n'+'\n'.join(
            '- 第'+str(x['day'])+'天：'+x.get('message','')+' 执行条件：'+x.get('trigger','')+'；停止条件：'+x.get('stop_if','') for x in plan.get('followups',[]))
    if not text.strip():
        text = record.get('markdown') or json.dumps(record,ensure_ascii=False,indent=2)
    return watermark+text+'\n\n---\nAI 辅助生成；发布前请完成事实、图片授权与平台标识核验。'


@app.get('/api/runs/{run_id}/export')
async def export(run_id:str,format:str='md'):
    from .run_records import load_run
    record = runs.get(run_id) or load_run(run_id)
    if not record:
        raise HTTPException(404,'运行记录不存在或服务已重启')
    if format not in ('md','html','json'):
        raise HTTPException(422,'format 必须是 md、html 或 json')
    if record.get('status') in ('blocked','error','needs_input'):
        raise HTTPException(409,'任务被阻断或需要补充信息，不能导出为营销稿')
    headers={'Content-Disposition':f'attachment; filename="zhijing-{run_id}.{format}"',
             'Cache-Control':'no-store'}
    text=markdown_export(record)
    if format=='json':
        return JSONResponse({**record,'export_notice':'草稿不可发布' if record.get('status')!='ready' else '已通过系统核验，发布前人工确认'},headers=headers)
    if format=='html':
        if (record.get('creative_plan') or {}).get('cards'):
            from .creative_export import render_creative_html, embed_card_assets
            return HTMLResponse(render_creative_html(record,await embed_card_assets(record)),headers=headers)
        return HTMLResponse('<!doctype html><meta charset="utf-8"><title>场景有据内容导出</title><pre style="white-space:pre-wrap;max-width:850px;margin:40px auto">'+html.escape(text)+'</pre>',headers=headers)
    return Response(text,media_type='text/markdown; charset=utf-8',headers=headers)


@app.get('/api/report')
async def report():
    path=ROOT/'docs'/'PLAN.html'
    if not path.is_file():
        raise HTTPException(404,'方案文档尚未生成')
    return FileResponse(path,media_type='text/html')


@app.get('/health')
async def health():
    return {'ok':True}


# Mount last: API routes must retain precedence.
from .poster_routes import router as poster_router
app.include_router(poster_router)
from .creative_routes import router as creative_router
app.include_router(creative_router)
from .media_routes import router as media_router
from .campaign_routes import router as campaign_router
from .product_routes import router as product_router
app.include_router(media_router)
app.include_router(campaign_router)
app.include_router(product_router)

if (ROOT/'web').is_dir():
    app.mount('/',StaticFiles(directory=ROOT/'web',html=True),name='web')
