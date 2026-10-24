"""HTTP boundary: local-only credential setup and evidence-gated exports."""
import asyncio
import html
import ipaddress
import json
import time
from collections import OrderedDict, defaultdict, deque
from contextlib import asynccontextmanager
from pathlib import Path
from urllib.parse import urlparse

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, ConfigDict, Field

from .config import ROOT, settings
from .ima import ImaProvider, ImaError
from .models import TaskInput

@asynccontextmanager
async def lifespan(application):
    application.state.runtime_started_at = time.time()
    from .generation_jobs import start_workers, stop_workers
    from .gtm_agent.jobs import start_agent_workers, stop_agent_workers
    await start_workers()
    await start_agent_workers()
    try:
        yield
    finally:
        from .channel_routes import stop_draft_preparations
        await stop_draft_preparations()
        await stop_agent_workers()
        await stop_workers()


app = FastAPI(title='场景有据 · 华为 PC 营销智能体', docs_url=None, redoc_url=None, lifespan=lifespan)
ima = ImaProvider(settings)
runs = OrderedDict()
semaphore = asyncio.Semaphore(2)
requests_by_client = defaultdict(deque)
demo_provider = None
local_provider = None


def local_settings_allowed(request):
    from .tenancy import get_context
    context = get_context()
    if context and not context.is_legacy and 'platform_admin' not in context.roles:
        return False
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
    from .resource_access import request_budget_key
    key = 'intake:' + request_budget_key(request)
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
    from .resource_access import validate_task_resources
    from .generation_jobs import generation_budget
    validate_task_resources(task)
    with generation_budget():
        from .store_task_research import prepare_store_task
        from .job_progress import report
        report('retrieval', {'phase': 'store_research'})
        try:
            task = await prepare_store_task(task)
        except TimeoutError:
            raise HTTPException(504, '门店研究用时较长，可以重试这次创作') from None
        except ValueError:
            raise HTTPException(422, '门店研究尚未形成明确方向，请补充所在城市或街道后重试') from None
        return await _execute_run_task(task, request)


async def _execute_run_task(task: TaskInput, request: Request):
    from .workflow import AgentWorkflow
    from .llm import ModelCallError
    from .creative_connectors import get_connector
    from .models import EvidenceBundle
    from .resource_access import validate_task_resources, request_budget_key
    validate_task_resources(task)
    try:
        connector=get_connector(task.platform,task.creative_connector)
        connector.build_brief(task,EvidenceBundle(product=task.product),{})
    except (KeyError,ValueError):
        raise HTTPException(422,'创意连接器或风格与所选平台不匹配') from None
    key = request_budget_key(request)
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
    if task.ecosystem_products or task.media_ids or task.assignment_id:
        from .task_context import TaskContextProvider
        provider = TaskContextProvider(provider, task)
    # Optional concept exploration uses at most two calls including its format
    # retry, reserving four for writing, review, one repair and final review.
    # All format retries share this same bounded budget.
    from .content_models import build_content_model
    # Reasoning content models can spend longer on a single structured draft.
    # Keep the enclosing 300-second workflow deadline and call budget unchanged.
    from .content_models import REASONING_CONTENT_MODELS
    explicit_content = all((settings.content_base_url, settings.content_api_key, settings.content_model))
    content_timeout = (150 if settings.content_model in REASONING_CONTENT_MODELS else 90) if explicit_content else 60
    try:
        llm = build_content_model(calls=12, tokens=5000, timeout=content_timeout, configuration=settings)
    except ModelCallError:
        raise HTTPException(503, '创作服务连接尚未完整设置，请联系团队管理员') from None
    try:
        async with semaphore:
            result = await asyncio.wait_for(AgentWorkflow(provider,llm).run(task),timeout=300)
        validate_task_resources(task)
    except HTTPException:
        raise
    except asyncio.TimeoutError:
        from .content_models import content_timing_snapshot
        timing = content_timing_snapshot(llm)
        # Keep completed review evidence in the same private/store-scoped turn.
        # This is diagnostic data, never an approved artifact or export.
        from .gtm_agent.effects import _claim, assert_agent_lease
        claim = _claim.get()
        if claim:
            diagnostics = {'timing': timing, 'approved': False,
                'draft': getattr(llm, 'last_reviewed_draft', None),
                'marketing_review': getattr(llm, 'marketing_review', None),
                'product_claim_review': getattr(llm, 'product_claim_review', None)}
            if len(json.dumps(diagnostics, ensure_ascii=False)) <= 100000:
                from .gtm_agent.repository import update_turn
                assert_agent_lease()
                update_turn(claim[0], lambda body: {**body, 'generation_diagnostics': diagnostics})
        import logging
        logging.getLogger(__name__).warning('Content workflow timeout: %s', json.dumps(timing))
        raise HTTPException(504, {'code': 'content_workflow_timeout',
            'message': '这次生成等待超时，需求已保留。', 'timing': timing}) from None
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
        from .campaign_evidence import public_reference_url
        from .vmall_catalog import valid_vmall_source
        public_source = (source.get('provider') == 'huawei_official' and official_url(source.get('url',''))) or (
            source.get('provider') == 'campaign_reference' and public_reference_url(source.get('url',''))) or (
            source.get('provider') == 'vmall_storefront' and valid_vmall_source(source))
        if not public_source:
            source['url'] = ''
        quotes=[fact.get('quote','') for fact in record.get('facts',[])
                if fact.get('source_id')==source.get('id') and fact.get('quote')]
        quoted_excerpt = '\n\n'.join(dict.fromkeys(quotes))
        # VMALL parameter excerpts remain authoritative inputs to image
        # production. Cutting them can orphan facts near the end of the SKU.
        source['excerpt'] = (quoted_excerpt if source.get('provider') == 'vmall_storefront'
                             else quoted_excerpt[:8000]) if quotes else '仅展示本次引用的资料名称，不公开资料全文。'
        if source.get('provider') == 'vmall_storefront' and source.get('excerpt_sha256'):
            import hashlib
            source['excerpt_sha256'] = hashlib.sha256(source['excerpt'].encode('utf8')).hexdigest()
    run_id = record.get('id') or record.get('run_id')
    if run_id:
        runs[run_id] = record
        from .generation_jobs import persist_generation_result
        from .tenancy import get_context
        from .tenancy.database import transaction_context
        context = get_context()
        snapshot = record.get('strategy', {}).get('strategy_snapshot')
        with transaction_context():
            persist_generation_result(run_id, record)
            if snapshot and context and not context.is_legacy:
                from .strategy_registry import freeze_strategy_snapshot
                frozen = freeze_strategy_snapshot(snapshot, run_id=run_id,
                    store_id=task.store_id or None, assignment_id=task.assignment_id)
                record['strategy']['snapshot_id'] = frozen['id']
                persist_generation_result(run_id, record)
        while len(runs)>100:
            runs.popitem(last=False)
    return JSONResponse(record)


@app.get('/api/runs/{run_id}')
async def read_run(run_id: str):
    from .run_records import get_run
    record = get_run(run_id)
    if record is None:
        raise HTTPException(404, '没有找到这次创作，可以用保留的需求重新开始')
    return JSONResponse(run_view(record, run_id), headers={'Cache-Control':'private, no-store'})


def run_view(record, run_id):
    from .tenancy import get_context
    context = get_context()
    if context is None or context.is_legacy:
        return record
    return {**record, 'assets': [{**item, 'url': f'/api/runs/{run_id}/assets/{item["id"]}' if item['id'].startswith('upload-') else item.get('url', '')}
                                for item in record.get('assets', [])]}


@app.get('/api/runs/{run_id}/assets/{asset_id}')
async def run_asset(run_id: str, asset_id: str):
    from .tenancy import authorize_resource
    from .parent_assets import read_parent_asset
    meta = authorize_resource('run', run_id)
    return await read_parent_asset(meta, meta.get('body') or {}, asset_id)


@app.get('/api/assets/{asset_id}')
async def asset(asset_id: str):
    if not asset_id or len(asset_id)>120 or '/' in asset_id or '..' in asset_id:
        raise HTTPException(404,'素材不存在')
    try:
        if asset_id.startswith('upload-'):
            from .resource_access import authorize_if_scoped
            authorize_if_scoped('media', asset_id.removeprefix('upload-'))
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
    from .run_records import get_run
    record = get_run(run_id)
    if not record:
        raise HTTPException(404,'运行记录不存在或服务已重启')
    if format not in ('md','html','json'):
        raise HTTPException(422,'format 必须是 md、html 或 json')
    if record.get('status') in ('blocked','error','needs_input'):
        raise HTTPException(409,'任务被阻断或需要补充信息，不能导出为营销稿')
    from .tenancy import get_context, audit
    if get_context() is not None:
        audit('creation.exported', 'run', run_id, {'format': format})
        from .strategy_registry import record_workflow_event
        record_workflow_event(run_id, 'exported')
    headers={'Content-Disposition':f'attachment; filename="zhijing-{run_id}.{format}"',
             'Cache-Control':'no-store'}
    record = run_view(record, run_id)
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


@app.get('/api/runtime/status')
async def runtime_status():
    """Authenticated, non-secret proof of the loaded deployment settings."""
    import os
    from .tenancy import require_context
    require_context()
    fact = bool(settings.fact_review_use_planner)
    marketing = bool(settings.marketing_review_use_planner)
    return {'pid': os.getpid(), 'build_revision': os.getenv('MATEQUILL_BUILD_REVISION', ''),
        'started_at': getattr(app.state, 'runtime_started_at', None),
        'review_roles': {'fact': fact, 'marketing': marketing},
        'models': {'writer': settings.content_model or settings.llm_model,
                   'fact_review': settings.llm_model if fact else settings.content_model or settings.llm_model,
                   'marketing_review': settings.llm_model if marketing else settings.content_model or settings.llm_model,
                   'art_direction': settings.design_text_model,
                   'visual_review': settings.image_review_model}}


# Mount last: API routes must retain precedence.
from .poster_routes import router as poster_router
app.include_router(poster_router)
from .design_routes import router as design_router
app.include_router(design_router)
from .creative_routes import router as creative_router
app.include_router(creative_router)
from .strategy_routes import router as strategy_router
app.include_router(strategy_router)
from .media_routes import router as media_router
from .campaign_routes import router as campaign_router
from .product_routes import router as product_router
app.include_router(media_router)
app.include_router(campaign_router)
app.include_router(product_router)
from .tenant_routes import router as tenant_router
from .resource_routes import router as resource_router
from .channel_routes import router as channel_router
from .tenancy import install_tenancy
app.include_router(tenant_router)
app.include_router(resource_router)
app.include_router(channel_router)
from .channel_opportunity_routes import router as channel_opportunity_router
app.include_router(channel_opportunity_router)
from .daily_store_routes import router as daily_store_router
app.include_router(daily_store_router)
from .publication_routes import router as publication_router
app.include_router(publication_router)
from .quotation_routes import router as quotation_router
app.include_router(quotation_router)
from .generation_jobs import router as jobs_router
app.include_router(jobs_router)
from .creative_agent_routes import router as creative_agent_router
app.include_router(creative_agent_router)
from .gtm_agent.routes import router as gtm_agent_router
from .messaging.routes import router as messaging_router
app.include_router(gtm_agent_router)
app.include_router(messaging_router)
install_tenancy(app)

if (ROOT/'web').is_dir():
    app.mount('/',StaticFiles(directory=ROOT/'web',html=True),name='web')
