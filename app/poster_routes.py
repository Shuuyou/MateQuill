"""Poster orchestration: retained documents, explicit provenance and real exports."""
from __future__ import annotations
import asyncio
import base64
import hashlib
import json
import re
import time
from collections import OrderedDict
from pathlib import Path

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse, Response
from pydantic import BaseModel, ConfigDict, Field

from .config import ROOT, settings
from .models import TaskInput
from .poster import PosterBrief, PosterDocument, build_poster, render_svg, apply_edits
from .vector_design import compose_vector_poster

router = APIRouter()
documents = OrderedDict()
poster_gate = asyncio.Semaphore(2)
_model_cache = {'fingerprint':'','models':[],'tested':{}}


class PosterRequest(BaseModel):
    model_config = ConfigDict(extra='forbid')
    task: TaskInput
    brief: PosterBrief = Field(default_factory=PosterBrief)
    source_run_id: str = Field(default='',max_length=100)


class PosterUpdate(BaseModel):
    model_config = ConfigDict(extra='forbid')
    document: dict | None = None
    edits: list[dict] = Field(default_factory=list,max_length=100)


def _image_client(model=None):
    from .image_service import ImageService
    return ImageService(settings.image_base_url,settings.image_api_key,model or settings.image_model)


@router.get('/api/image-models')
async def image_models():
    if not settings.image_api_key:
        return {'configured':False,'models':[],'selected_model':settings.image_model}
    fingerprint=hashlib.sha256((settings.image_base_url+settings.image_api_key).encode()).hexdigest()
    if _model_cache['fingerprint']!=fingerprint:
        try:
            rows=await _image_client().list_models()
        except Exception:
            raise HTTPException(502,'模型列表暂时无法读取，请检查图像服务连接') from None
        # Names are discovery hints, never proof of endpoint capability.
        pattern=re.compile(r'gpt.*image|seedream|gemini.*(?:image|banana)|flux|recraft|qwen-image|minimax',re.I)
        _model_cache.update(fingerprint=fingerprint,models=[r for r in rows if pattern.search(r.get('id',''))],tested={})
    rows=[{**r,'generation_tested':r['id'] in _model_cache['tested']} for r in _model_cache['models']]
    return {'configured':True,'models':rows,'selected_model':settings.image_model,
            'note':'列表来自服务商；能否通过生图端点使用，以实际请求结果为准。'}


def _path(pid):
    if not re.fullmatch(r'[a-f0-9]{32}',pid):
        raise HTTPException(404,'海报不存在')
    return ROOT/'runtime'/'posters'/(pid+'.json')


def _save(doc, expected_document_version=None):
    serialized=doc.model_dump_json()
    if len(serialized.encode('utf-8'))>40_000_000:
        raise HTTPException(422,'海报素材总量超过保存预算，请改用较小的产品素材')
    from .tenancy import get_context, save_body
    context = get_context()
    if context and not context.is_legacy:
        if expected_document_version is None:
            save_body('poster', doc.id, doc.model_dump(mode='json'))
        else:
            from .tenancy import authorize_resource, service_update_body
            meta = authorize_resource('poster', doc.id, 'write')
            if (meta.get('body') or {}).get('version') != expected_document_version:
                raise HTTPException(409, '海报刚刚已更新，请刷新后重试')
            service_update_body('poster', doc.id, doc.model_dump(mode='json'), expected_version=meta['version'])
        return
    documents[doc.id]=doc
    while len(documents)>20:
        documents.popitem(last=False)
    path=_path(doc.id)
    path.parent.mkdir(parents=True,exist_ok=True)
    temporary=path.with_suffix('.tmp')
    temporary.write_text(serialized,encoding='utf-8')
    temporary.replace(path)


def _get(pid):
    from .tenancy import get_context, load_body
    context = get_context()
    if context and not context.is_legacy:
        _path(pid)  # Validate the external identifier before any lookup.
        body = load_body('poster', pid)
        if body is None:
            raise HTTPException(404, '海报不存在')
        return PosterDocument.model_validate(body)
    if pid in documents:
        return documents[pid]
    path=_path(pid)
    if not path.is_file() or path.stat().st_size>40_000_000:
        raise HTTPException(404,'海报不存在或已过期')
    try:
        doc=PosterDocument.model_validate_json(path.read_text(encoding='utf-8'))
    except ValueError:
        raise HTTPException(409,'海报记录无法读取') from None
    documents[pid]=doc
    while len(documents)>20:
        documents.popitem(last=False)
    return doc


def _response(doc):
    return {'id':doc.id,'document':doc.model_dump(),'status':doc.status,'audit':doc.audit,
            'source_run_id':doc.source_run_id,'preview_url':f'/api/posters/{doc.id}/preview.svg?v={doc.version}',
            'exports':{kind:f'/api/posters/{doc.id}/export?format={kind}' for kind in ('png','svg','json')}}


async def _visual_review(doc):
    if not settings.image_api_key or not settings.image_review_model:
        doc.audit.append({'severity':'warning','category':'visual_review','message':'自动视觉审稿未配置，成品需人工检查。'})
        return
    from .poster_render import render_png
    from .poster_critic import review_poster
    try:
        png=await asyncio.wait_for(asyncio.to_thread(render_png,render_svg(doc),doc.width,doc.height),timeout=55)
        review=await review_poster(png,doc,settings.image_base_url,settings.image_api_key,settings.image_review_model)
        doc.audit.extend(review)
        status=next((item for item in review if item.get('category')=='visual_review_status'),{})
        safe=status.get('safe_layer_edits') or []
        if status.get('review_completed') and safe:
            try:
                updated=apply_edits(doc,safe[:3])
                doc.layers=updated.layers
                doc.version=updated.version
                doc.audit.append({'severity':'warning','category':'visual_repair',
                                  'message':'已按成品视觉审阅完成一次非文字布局修复，修复结果仍需人工确认。'})
            except ValueError:
                doc.audit.append({'severity':'warning','category':'visual_repair','message':'视觉布局建议未通过几何检查，已保留原稿。'})
    except Exception:
        doc.audit.append({'severity':'warning','category':'visual_review','message':'成品视觉审稿未完成，不能视为已通过像素核验。'})


async def poster_source(task,request,brief=None):
    """A poster has a single-screen brief, never an article's carousel plan."""
    from . import api
    from .poster_copy import create_poster_copy
    from .llm import OpenAICompatibleLLM
    from .resource_access import validate_task_resources
    validate_task_resources(task)
    from .resource_access import request_budget_key
    key='poster:'+request_budget_key(request)
    history=api.requests_by_client[key]
    now=time.monotonic()
    while history and now-history[0]>60:
        history.popleft()
    if len(history)>=10:
        raise HTTPException(429,'海报请求较多，请稍后再试')
    history.append(now)
    provider=api.get_demo() if task.mode=='demo' else api.get_local() if task.mode=='local' else api.ima
    llm=OpenAICompatibleLLM(settings.llm_base_url,settings.llm_api_key,settings.llm_model)
    try:
        result=await asyncio.wait_for(create_poster_copy(task,provider,llm,brief),timeout=150)
        validate_task_resources(task)
    except asyncio.TimeoutError:
        raise HTTPException(504,'单屏海报文案生成超时，请重试') from None
    record=result.model_dump(mode='json')
    for asset in record.get('assets',[]):
        asset.pop('local_path',None)
    for source in record.get('sources',[]):
        source['url']=''
        source['excerpt']='\n\n'.join(dict.fromkeys(f.get('quote','') for f in record.get('facts',[])
            if f.get('source_id')==source.get('id')))[:8000]
    record['_poster_task']=task.model_dump()
    from .run_records import save_run
    save_run(record['run_id'], record)
    api.runs[record['run_id']]=record
    while len(api.runs)>100:
        api.runs.popitem(last=False)
    return JSONResponse(record)


@router.post('/api/posters')
async def create_poster(payload: PosterRequest, request: Request):
    from .generation_jobs import generation_budget
    with generation_budget():
        return await _create_poster(payload, request)


async def _create_poster(payload: PosterRequest, request: Request):
    from . import api
    task=payload.task.model_copy(update={'platform':'poster'})
    from .resource_access import validate_task_resources
    validate_task_resources(task)
    brief=payload.brief.model_copy(deep=True)
    brief.background_asset_id=''
    if task.mode=='live' and brief.background_mode=='generate':
        raise HTTPException(422,'ima 严格图源模式请使用纯色设计背景；AI 背景可在独立商业设计模式使用')
    async with poster_gate:
        from .run_records import get_run
        source=get_run(payload.source_run_id) if payload.source_run_id else None
        if source is not None:
            if source.get('_poster_task') != task.model_dump():
                raise HTTPException(409,'原任务已变化，请重新生成文案')
        else:
            response=await poster_source(task,request,brief)
            source=json.loads(response.body)
            if source.get('run_id') in api.runs:
                api.runs[source['run_id']]['_poster_task']=task.model_dump()
        if source.get('status') in ('error','needs_input') or not source.get('blocks'):
            return {'status':source.get('status','blocked'),'questions':source.get('questions',[]),
                    'audit':source.get('audit',[]),'source_run':source}
        errors=[a for a in source.get('audit',[]) if a.get('severity')=='error']
        if errors:
            return {'status':'blocked','questions':[],'audit':source.get('audit',[]),'source_run':source}
        source={**source,'assets':[dict(a) for a in source.get('assets',[])]}
        doc=(compose_vector_poster(task,source,brief) if brief.background_mode=='solid'
             else build_poster(task,source,brief))
        # Fetch exactly the already selected local/ima source, never client URLs.
        for layer in doc.layers:
            if layer.role!='product':
                continue
            reply=await api.asset(layer.asset_id)
            if hasattr(reply,'body'):
                raw=reply.body
            else:
                raw=await asyncio.to_thread(Path(reply.path).read_bytes)
            from .poster import _raster_data
            data_url=_raster_data(raw)
            if not data_url:
                raise HTTPException(422,'产品素材不是有效的可嵌入图片')
            doc.assets[layer.asset_id]['data_url']=data_url
        if doc.status=='blocked':
            validate_task_resources(task)
            _save(doc)
            return _response(doc)
        if brief.background_mode=='generate':
            if not settings.image_api_key:
                raise HTTPException(422,'请先在连接设置中配置生图服务，或选择纯色背景')
            model=brief.image_model or settings.image_model
            prompt=(f'Commercial consumer-electronics poster background only. Art direction: {brief.direction}. '
                    f'Style: {brief.style or "restrained premium studio, subtle sculptural light"}. '
                    f'Palette: {brief.background_color}, {brief.accent_color}. '
                    'Keep the central product placement and top headline regions uncluttered. '
                    'No laptop, no device, no product, no typography, no logo, no price, no QR code.')
            width,height=(1536,1024) if doc.width>doc.height else (1024,1536) if doc.height/doc.width>1.2 else (1024,1024)
            try:
                image=await _image_client(model).generate_background(prompt,width,height)
            except Exception as exc:
                from .image_service import ImageServiceError
                code=getattr(exc,'code','upstream_failure') if isinstance(exc,ImageServiceError) else 'upstream_failure'
                raise HTTPException(502,f'背景生成未完成（{code}）；未用占位图伪装成功，可改选纯色后重试') from None
            _model_cache['tested'][model]=True
            aid='generated-'+hashlib.sha256(image.data).hexdigest()[:20]
            brief.background_asset_id=aid
            doc=build_poster(task,source,brief)
            # Reattach only the exact product source bytes already checked.
            for layer in doc.layers:
                if layer.role=='product':
                    doc.assets[layer.asset_id]['data_url']=data_url
            doc.assets[aid]={'data_url':f'data:{image.mime};base64,'+base64.b64encode(image.data).decode(),
                'provenance':'ai_generated_background','verified':False,'title':'AI生成背景（非商品实拍）',
                'model':image.model,'width':image.width,'height':image.height}
            doc.audit.append({'severity':'warning','category':'generated_background',
                              'message':'背景由图像模型生成，产品主体沿用原始素材；最终成品尚需视觉与事实复核。'})
        await _visual_review(doc)
        doc.audit.append({'severity':'warning','category':'ocr',
                          'message':'程序已检查图层边界；独立 OCR 逐字核验尚未完成，发布前请核对价格、型号与必要声明。'})
        validate_task_resources(task)
        _save(doc)
        return _response(doc)


@router.get('/api/posters/{pid}')
def read_poster(pid: str):
    return _response(_get(pid))


@router.patch('/api/posters/{pid}')
async def update_poster(pid: str, payload: PosterUpdate):
    from .resource_access import authorize_if_scoped
    authorize_if_scoped('poster', pid, 'write')
    old=_get(pid)
    edits=payload.edits
    if payload.document is not None:
        incoming=payload.document
        if incoming.get('version',old.version)!=old.version:
            raise HTTPException(409,'海报已有更新，请刷新画布后再修改')
        if set(incoming)-set(old.model_dump()) or incoming.get('canvas')!=old.canvas.model_dump():
            raise HTTPException(422,'尺寸变化请重新排版生成，不能修改原始素材或画布记录')
        incoming_layers=incoming.get('layers',[])
        if not isinstance(incoming_layers,list) or any(not isinstance(l,dict) or not isinstance(l.get('id'),str) for l in incoming_layers):
            raise HTTPException(422,'图层必须是有效的对象列表')
        if len(incoming_layers)!=len(old.layers) or {l.get('id') for l in incoming_layers}!={l.id for l in old.layers}:
            raise HTTPException(422,'不能添加、删除或替换图层')
        edits=[]
        lookup={l.id:l.model_dump() for l in old.layers}
        for layer in incoming_layers:
            before=lookup[layer['id']]
            changes={k:v for k,v in layer.items() if before.get(k)!=v}
            if changes:
                edits.append({'id':layer['id'],'changes':changes})
    try:
        doc=apply_edits(old,edits)
    except (ValueError,TypeError,KeyError):
        raise HTTPException(422,'修改无效：请保留锁定图层，避免文字溢出或元素超出画布') from None
    doc.status='draft'
    doc.audit.append({'severity':'warning','category':'edited','message':'手动修改后的文案与成品需重新核验，草稿标识保留。'})
    _save(doc, expected_document_version=old.version)
    return _response(doc)


@router.get('/api/posters/{pid}/preview.svg')
async def preview_poster(pid: str):
    return Response(render_svg(_get(pid)),media_type='image/svg+xml',headers={
        'Cache-Control':'no-store','X-Content-Type-Options':'nosniff',
        'Content-Security-Policy':"default-src 'none'; img-src data:; style-src 'unsafe-inline'; sandbox"})


@router.get('/api/posters/{pid}/export')
async def export_poster(pid: str, format: str='png'):
    doc=_get(pid)
    if format not in ('png','svg','json'):
        raise HTTPException(422,'支持 PNG、SVG 和 JSON 导出')
    if doc.status=='blocked':
        raise HTTPException(409,'画布仍有阻断问题，请先调整')
    headers={'Content-Disposition':f'attachment; filename="poster-{pid}.{format}"','Cache-Control':'no-store'}
    if format=='json':
        return JSONResponse(doc.model_dump(),headers=headers)
    svg=render_svg(doc)
    if format=='svg':
        return Response(svg,media_type='image/svg+xml',headers=headers)
    from .poster_render import render_png
    try:
        async with poster_gate:
            data=await asyncio.wait_for(asyncio.to_thread(render_png,svg,doc.width,doc.height),timeout=60)
    except Exception:
        raise HTTPException(503,'PNG 渲染不可用，请安装 Chromium 渲染依赖；SVG 和设计文档仍可导出') from None
    from .resource_access import ensure_live_context, authorize_if_scoped
    ensure_live_context()
    authorize_if_scoped('poster', pid)
    return Response(data,media_type='image/png',headers=headers)
