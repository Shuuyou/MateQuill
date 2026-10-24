"""Authorized, checkpointed image-set production on the shared creation queue."""
from __future__ import annotations

import asyncio
import base64
from copy import deepcopy
import hashlib
import json
import re
import time
from pathlib import Path
from uuid import uuid4

from fastapi import HTTPException
from fastapi.responses import JSONResponse

from .config import settings
from .job_progress import report
from .models import TaskInput
from .tenancy import authorize_resource, require_context, load_body, save_body
from .resource_access import ensure_live_context, validate_task_resources

PLATFORMS = {'xiaohongshu', 'moments', 'wechat', 'bilibili', 'douyin', 'poster'}


def _copy_auditor():
    """Use the configured independent reviewer for factual speech-act checks."""
    if not settings.image_api_key or not settings.image_review_model:
        return None
    from .content_models import ContentLLM
    # Match the explicitly selected reasoning reviewer's supported wire rather
    # than relying on legacy temperature/max_tokens defaults. This is still
    # the image role's own connection, not a content-service fallback.
    return ContentLLM(settings.image_base_url, settings.image_api_key,
        settings.image_review_model, max_calls=2, max_tokens=4000, timeout=90)


def _design_review_model():
    """Known multimodal art directors may also inspect actual design pixels.

    Keep asset observation and the independent copy auditor on their own route.
    An arbitrary text-only art model must not become a visual reviewer.
    """
    from .design_review import REASONING_VISUAL_MODELS
    return (settings.image_review_model or
            (settings.design_text_model if settings.design_text_model in REASONING_VISUAL_MODELS else ''))


def _design_model(fallback, *, calls, tokens):
    """Allow a dedicated art director without changing store text generation."""
    if settings.design_text_model and settings.image_api_key:
        from .design.llm import ArtDirectionLLM
        return ArtDirectionLLM(settings.image_base_url, settings.image_api_key,
            settings.design_text_model, max_calls=calls, max_tokens=tokens,
            timeout=150 if settings.design_text_model in {'gpt-6-astra', 'gpt-6.1-sol'} else 75)
    return fallback(calls=calls, tokens=tokens)


def _delivered_title(source, artboards):
    """Name the design after the reviewed cover, not an older text draft."""
    def label(text):
        # Display titles are single-line labels, not the canvas' soft wrapping.
        text = re.sub(r'(?<=[A-Za-z0-9])\r?\n(?=[A-Za-z0-9])', ' ', str(text))
        return text.replace('\r', '').replace('\n', '').strip()
    if artboards:
        document = artboards[0].get('document') or {}
        for layer in document.get('layers', []):
            if layer.get('type') == 'text' and layer.get('role') == 'headline' and layer.get('visible', True) and layer.get('text'):
                return label(layer['text'])
        for asset in (document.get('assets') or {}).values():
            provenance = asset.get('provenance')
            for row in (provenance.get('display_copy', []) if isinstance(provenance, dict) else []):
                if row.get('role') == 'headline' and row.get('text'):
                    return label(row['text'])
    return label(source.get('title') or '本店图片')


def _visual_marketing_context(source, task_data, store_context, identity):
    """Project current run evidence, never fabricate fresh news in the image stage."""
    from .marketing_review import public_marketing_text
    from .gtm_agent.place_context import PLACE_CONTEXT_MARKER
    from .design.publication import publication_strategy

    def bounded(value, depth=0):
        if depth > 9:
            return None
        if isinstance(value, str):
            return value[:1800]
        if isinstance(value, list):
            return [bounded(row, depth + 1) for row in value[:12]]
        if isinstance(value, dict):
            return {str(key): bounded(item, depth + 1) for key, item in list(value.items())[:35]}
        return value if value is None or isinstance(value, (int, float, bool)) else None

    public_copy = {key: str(source.get(key) or '')[:16000]
                   for key in ('title', 'intro', 'body', 'cta')}
    public_copy['blocks'] = [{'text': str(row.get('text') or '')[:4000],
                             **({'disclosures': deepcopy(row['disclosures'])} if row.get('disclosures') else {})}
                            for row in source.get('blocks', [])[:20] if isinstance(row, dict)]
    public_copy['images'] = [{'caption': str(row.get('caption') or '')[:1000]}
                            for row in source.get('images', [])[:12] if isinstance(row, dict)]
    creative = source.get('creative_plan') or {}
    public_copy['creative_plan'] = bounded({key: creative.get(key, []) for key in ('cards', 'test_variants')}) if isinstance(creative, dict) else {}
    public_copy['tags'] = bounded(source.get('tags', []))
    publication = publication_strategy(task_data.get('platform') or '小红书',
        task_data.get('design_surface', ''), task_data.get('design_instruction', ''), source)
    if publication.mode == 'standalone_poster':
        public_copy = {}
    elif publication.mode == 'xhs_title_only':
        public_copy = {'title': public_copy['title'], 'tags': public_copy['tags']}
    public_text = public_marketing_text(public_copy)
    facts = [row for row in source.get('facts', []) if isinstance(row, dict)]
    cited_ids = set()
    def cited(value):
        if isinstance(value, dict):
            for key, item in value.items():
                if key in {'fact_ids', 'supported_fact_ids'} and isinstance(item, list):
                    cited_ids.update(identifier for identifier in item if isinstance(identifier, str))
                elif isinstance(item, (dict, list)):
                    cited(item)
        elif isinstance(value, list):
            for item in value:
                cited(item)
    for key in ('blocks', 'creative_plan', 'images', '_design_copy'):
        cited(source.get(key, []))
    referenced = [row for row in facts if row.get('id') in cited_ids or
                  (str(row.get('value', '')) and str(row['value']) in public_text)]
    # A prose realization need not repeat a complete raw value verbatim. Keep
    # actually cited rows even beyond the old first-12 cut, with their complete
    # values, original spans and qualifications; missing context must not force
    # another paid rewrite of a correct statement.
    product_facts = deepcopy(referenced or facts[:12])
    if len(json.dumps(product_facts, ensure_ascii=False).encode('utf8')) > 100000:
        raise HTTPException(422, {'code': 'visual_review_evidence_budget',
                                 'message': '请减少本次图片中的产品主张后继续'})
    strategy = source.get('strategy') or {}
    if not isinstance(strategy, dict):
        strategy = {}
    research = strategy.get('marketing_context') or {}
    research = bounded(research) if isinstance(research, dict) else {}
    # Structured store evidence is supplied separately; don't repeat its large
    # serialized envelope inside requirements and invite conflicting versions.
    requirements = str(task_data.get('requirements') or '').split(PLACE_CONTEXT_MARKER, 1)[0][:1500]
    return {'publisher_identity': bounded(identity), 'store_research': bounded(store_context),
            'task': {'audience': str(task_data.get('audience') or '')[:700],
                     'scene': str(task_data.get('scene') or '')[:1000], 'requirements': requirements},
            'published_copy': public_copy, 'publication': publication.model_dump(),
            'product_facts': product_facts,
            **({'validated_product_claim_review': deepcopy(strategy['product_claim_review'])}
               if isinstance(strategy.get('product_claim_review'), dict) else {}),
            'marketing_context': research,
            'previous_text_review': bounded(strategy.get('marketing_review')),
            'text_review_history': bounded(strategy.get('marketing_review_history', [])[-3:]),
            'freshness_boundary': '仅使用以上带时间的当日检索与规则上下文；没有证据不得用模型记忆宣称当前舆论已核验。'}


def source_record(identifier):
    context = ensure_live_context() or require_context()
    if context.is_legacy or not context.store_id:
        raise HTTPException(401, '登录门店后，就可以继续设计')
    row = authorize_resource('run', identifier)
    record = row.get('body') or {}
    if row['store_id'] != context.store_id:
        raise HTTPException(409, '回到这份内容所属门店后继续设计')
    if record.get('status') != 'ready' or not record.get('_poster_task'):
        raise HTTPException(409, '先完成这份内容，再为它制作图片')
    task = TaskInput.model_validate(record['_poster_task'])
    validate_task_resources(task)
    return record, task


def load_design(identifier, *, edit=False):
    row = authorize_resource('design', identifier, 'edit' if edit else 'read')
    if row['store_id'] != require_context().store_id:
        raise HTTPException(409, '回到这份作品所属门店后继续查看')
    record = row.get('body') or {}
    source_record(record.get('source_run_id', ''))
    return record


def project_design(record):
    identifier = record['id']
    return {'id': identifier, 'title': record['title'], 'platform': record['platform'],
            'version': record['version'], 'status': record['status'],
            'source_run_id': record['source_run_id'], 'parent_id': record.get('parent_id'),
            'artboards': [{'id': board['id'], 'role': board['role'], 'title': board['title'],
                'preview_url': f'/api/designs/{identifier}/artboards/{board["id"]}/preview.svg',
                'export_urls': {fmt: f'/api/designs/{identifier}/artboards/{board["id"]}/export?format={fmt}'
                                for fmt in ('png', 'svg', 'json')}} for board in record['artboards']]}


def enqueue_design(source_run_id, *, platform='', surface='', instruction='', idempotency_key=None, parent_id=None, craft_edit=None):
    source, task = source_record(source_run_id)
    parent = load_design(parent_id, edit=True) if parent_id else None
    if parent and parent['source_run_id'] != source_run_id:
        raise HTTPException(409, '请选择同一份内容的设计')
    target = platform or (parent['platform'] if parent else task.platform)
    surface = surface or (parent.get('surface', '') if parent else '')
    if target not in PLATFORMS:
        raise HTTPException(422, '请选择支持的发布平台')
    from .design import get_platform_connector
    try:
        get_platform_connector(target, surface)
    except ValueError:
        raise HTTPException(422, '请选择支持的图片发布入口') from None
    parameters = {'source_run_id': source_run_id, 'platform': target, 'surface': surface,
                  'instruction': instruction, 'parent_id': parent_id}
    if craft_edit is not None:
        from .design.craft_controls import CraftEditInput
        if not parent:
            raise HTTPException(422,'请选择要精修的图片')
        parameters['craft_edit'] = CraftEditInput.model_validate(craft_edit).model_dump(mode='json')
    # A stable work ID also checkpoints paid tool stages across worker recovery.
    key = idempotency_key or uuid4().hex
    digest = hashlib.sha256((require_context().tenant_id + ':' + require_context().user_id + ':' + key).encode()).hexdigest()
    parameters['design_id'] = digest[:32]
    from .generation_jobs import JobInput, enqueue_operation
    return enqueue_operation(JobInput(task=task, idempotency_key=key), operation='design', parameters=parameters)


async def collect_assets(source_run_id, source):
    """Read exact authorized source assets. Never accept client paths or URLs."""
    from .api import run_asset, asset as catalog_asset
    from .poster import _raster_data
    selected = {row.get('asset_id') for row in source.get('images', [])}
    selected.update(aid for card in (source.get('creative_plan') or {}).get('cards', []) for aid in card.get('asset_ids', []))
    # User photographs belonging to this source task are also valid inputs.
    selected.update('upload-' + mid for mid in source.get('_poster_task', {}).get('media_ids', []))
    result, total = {}, 0
    declared = {row['id']: row for row in source.get('assets', [])}
    task = source.get('_poster_task', {})
    products = {task.get('product'), *task.get('ecosystem_products', [])}
    additional = [key for key, item in declared.items() if key not in selected
                  and not key.startswith('upload-') and item.get('product') in products
                  and item.get('verified') and item.get('provenance') in {'huawei_official', 'vmall_storefront', 'local_archive', 'ima_direct'}]
    for identifier in list(dict.fromkeys([*sorted(selected - {None}), *additional]))[:8]:
        if identifier not in declared:
            continue
        response = await run_asset(source_run_id, identifier) if identifier in selected else await catalog_asset(identifier)
        raw = await asyncio.to_thread(Path(response.path).read_bytes) if hasattr(response, 'path') else response.body
        total += len(raw)
        if len(raw) > 8_000_000 or total > 20_000_000:
            continue
        data = _raster_data(raw)
        if not data:
            continue
        item = {key: value for key, value in declared[identifier].items() if key not in {'url', 'local_path'}}
        expected = item.get('sha256')
        if expected and expected != hashlib.sha256(raw).hexdigest():
            raise HTTPException(409, '产品素材已更新，请刷新资料后继续设计')
        item.update(data_url=data, sha256=hashlib.sha256(raw).hexdigest())
        from .design_image_tools import read_image
        image = read_image(raw)
        box = image.getchannel('A').getbbox()
        item['pixel_budget'] = {'canvas_width': image.width, 'canvas_height': image.height,
            'effective_width': box[2] - box[0] if box else 0,
            'effective_height': box[3] - box[1] if box else 0}
        item.update(width=image.width, height=image.height,
                    effective_width=item['pixel_budget']['effective_width'],
                    effective_height=item['pixel_budget']['effective_height'])
        if item.get('provenance') in {'user_photo', 'store_upload', 'real_photo', 'authorized_photo'}:
            item['visual_role'] = 'store_photo'
        result[identifier] = item
    return result


async def render_document(document):
    from .design import render_design_svg
    from .design.renderer import try_full_frame_png
    from .poster_render import render_png
    original = await asyncio.to_thread(try_full_frame_png, document,
        lambda aid: document.assets.get(aid, {}).get('data_url', ''))
    if original is not None:
        return original
    svg = await asyncio.to_thread(render_design_svg, document,
        lambda aid: document.assets.get(aid, {}).get('data_url', ''))
    return await asyncio.to_thread(render_png, svg, document.width, document.height)


def _checkpoint(identifier, value):
    from .generation_jobs import assert_job_lease
    from .gtm_agent.effects import assert_agent_lease
    ensure_live_context()
    assert_job_lease()
    assert_agent_lease()
    if len(json.dumps(value, ensure_ascii=False).encode()) > 45_000_000:
        raise HTTPException(422, '这组图片较大，请减少素材后再试')
    save_body('design_work', identifier, value)


async def _design_elements_treatment(identifier, state, plan, board, assets):
    """Generate a checkpointed abstract design plate; retain real product layers."""
    from .design.native_visual import native_visual_contract
    concepts = plan.get('concepts', []) if isinstance(plan, dict) else getattr(plan, 'concepts', [])
    concept = next((item for item in concepts if (item.get('id') if isinstance(item, dict) else item.id) == board.concept_id), None)
    brief = plan.get('brief') if isinstance(plan, dict) else getattr(plan, 'brief', None)
    instruction = brief.get('instruction', '') if isinstance(brief, dict) else getattr(brief, 'instruction', '')
    treatment = concept.get('treatment', '') if isinstance(concept, dict) else getattr(concept, 'treatment', '')
    production_method = concept.get('production_method', 'auto') if isinstance(concept, dict) else getattr(concept, 'production_method', 'auto')
    if production_method in {'native_assembly', 'source_photo'}:
        # Product arrays, typography, source photography and native light/shadow
        # are complete design choices. A transparent subject is not a request
        # for a paid decorative plate. Final actual-render reviews still run.
        from .design.source_integrity import native_source_proof
        trace={'method':production_method,'new_image_generated':False,
               'visual_thesis':concept.get('visual_thesis','') if isinstance(concept,dict) else getattr(concept,'visual_thesis',''),
               'source_integrity':native_source_proof(board.document,assets),
               'scope':'Authored native composition; actual-render and marketing review remain pending.'}
        if state.setdefault('native_design_composition',{}).get(board.id)!=trace:
            state['native_design_composition'][board.id]=trace
            _checkpoint(identifier,state)
        return board.document
    role = getattr(board, 'role', '')
    contract = native_visual_contract(brief, role=role, assets=assets)
    photographs = [layer for layer in board.document.layers if layer.type == 'image'
                   and layer.visible and layer.role == 'photograph'
                   and layer.asset_id in contract['authorized_photo_ids']]
    if (contract['selected'] == 'source_observation' and photographs and concept is not None
            and (role == 'photo' or treatment == 'photo'
                 or any(token in instruction for token in ('保留原图', '保留照片', '不要重画', '不要换背景')))):
        # Preserve the actual native source layers, rather than paying for an
        # abstract plate that would turn an existing scene into another poster.
        # Existing copy, geometry, source and final-pixel gates remain downstream.
        from .design.source_integrity import native_source_proof
        proof = native_source_proof(board.document, assets)
        trace = {'method': 'source_photo_native_composition', 'native_visual_contract': contract,
                 'source_integrity': proof, 'new_image_generated': False,
                 'product_identity_verified': False,
                 'scope': 'Authorized original image bytes with recorded display transforms; final composition still requires review.'}
        if state.setdefault('source_preservation', {}).get(board.id) != trace:
            state['source_preservation'][board.id] = trace
            _checkpoint(identifier, state)
        return board.document
    if not (settings.image_api_key and settings.image_model
            and any(value.get('prepared_subject') for value in board.document.assets.values())):
        return board.document
    from .design.elements import generate_design_elements, attach_design_elements, plan_design_elements
    from .design_review import review_design_element, accepted_design_element_review
    from .design.llm import ArtDirectionLLM
    from .llm import ModelCallError
    from .image_service import ImageService, ImageServiceError
    entries = state.setdefault('design_elements', {})
    entry = entries.get(board.id)
    if entry and entry.get('status') == 'rejected':
        raise HTTPException(422, {'code': 'design_elements_rejected', 'design_id': identifier})
    if entry and entry.get('status') != 'ready':
        raise HTTPException(503, {'code': 'design_elements_attempt_incomplete', 'design_id': identifier})
    if not entry:
        if len(entries) >= 4:
            raise HTTPException(429, {'code': 'design_elements_attempt_budget', 'design_id': identifier})
        entries[board.id] = {'status': 'started', 'started_at': time.time()}
        _checkpoint(identifier, state)
        try:
            director = ArtDirectionLLM(settings.image_base_url, settings.image_api_key,
                settings.design_text_model or settings.image_review_model,
                max_calls=2, max_tokens=2200, timeout=150)
            direction = await plan_design_elements(plan, board, director)
            entries[board.id]['element_direction'] = direction
            _checkpoint(identifier, state)
            metadata = await generate_design_elements(plan, board,
                ImageService(settings.image_base_url, settings.image_api_key, settings.image_model, timeout=240),
                element_brief=direction)
        except ImageServiceError as exc:
            entries[board.id].update(status='incomplete', reason=exc.reason)
            _checkpoint(identifier, state)
            raise HTTPException(503, {'code': 'design_elements_incomplete', 'design_id': identifier}) from None
        except (ModelCallError, ValueError):
            entries[board.id].update(status='incomplete', reason='element_direction_incomplete')
            _checkpoint(identifier, state)
            raise HTTPException(503, {'code': 'design_elements_incomplete', 'design_id': identifier}) from None
        entry = entries[board.id] = {'status': 'ready', 'asset': metadata,
            'element_direction': direction}
        _checkpoint(identifier, state)
    metadata = entry['asset']
    inspection = entry.get('pixel_review')
    if (not inspection or inspection.get('reviewed_artifact_sha256') != metadata.get('sha256')
            or inspection.get('protocol_version') != 1
            or inspection.get('review_scope') != 'standalone_design_element'):
        inspection = await review_design_element(metadata, settings.image_base_url,
            settings.image_api_key, _design_review_model())
        entry['pixel_review'] = inspection
        if inspection.get('review_completed') is True and inspection.get('action') == 'reject':
            entry['status'] = 'rejected'
        _checkpoint(identifier, state)
    if not accepted_design_element_review(inspection, metadata.get('sha256')):
        code = 'design_elements_rejected' if entry['status'] == 'rejected' else 'design_elements_review_incomplete'
        raise HTTPException(422 if entry['status'] == 'rejected' else 503,
            {'code': code, 'design_id': identifier})
    state.setdefault('element_pixel_reviews', {})[metadata['sha256']] = inspection
    assets[metadata['id']] = metadata
    return attach_design_elements(board.document, metadata)


async def _audit_embedded_elements(identifier, state, document):
    """Old completed boards must satisfy the same pixel gate as new work."""
    from .poster import PosterDocument
    from .design_review import review_design_element, accepted_design_element_review
    doc = document if isinstance(document, PosterDocument) else PosterDocument.model_validate(document)
    reviews = state.setdefault('element_pixel_reviews', {})
    for layer in doc.layers:
        if not layer.visible or layer.type != 'image' or layer.role != 'design_element':
            continue
        metadata = doc.assets.get(layer.asset_id) or {}
        digest = metadata.get('sha256')
        review = reviews.get(digest) or {}
        bound = (review.get('protocol_version') == 1
            and review.get('review_scope') == 'standalone_design_element'
            and review.get('reviewed_artifact_sha256') == digest)
        if not bound:
            review = await review_design_element(metadata, settings.image_base_url,
                settings.image_api_key, _design_review_model())
            if digest:
                reviews[digest] = review
            _checkpoint(identifier, state)
        if not accepted_design_element_review(review, digest):
            raise HTTPException(422, {'code': 'embedded_design_elements_need_revision',
                'design_id': identifier})


def _validate_delivered_copy(document, prepared, *, instruction=''):
    """An art pass may reflow approved wording, never silently replace the hook."""
    from .design.publication import no_overlay_text_requested
    if no_overlay_text_requested(instruction):
        if any(layer.visible and layer.type == 'text' and layer.text for layer in document.layers):
            raise HTTPException(422, {'code': 'unexpected_overlay_text_in_wordless_design',
                                     'message': '本次要求无叠字，画面仍含新增文字，原素材已保留'})
        return
    if prepared.get('status') != 'approved' or not prepared.get('audit', {}).get('completed'):
        return
    compact = lambda value: re.sub(r'\s+', '', str(value))
    expected = {compact(card.get('headline', '')) for card in prepared.get('cards', [])
                if card.get('headline')}
    actual = {compact(layer.text) for layer in document.layers
              if layer.visible and layer.type == 'text' and layer.text}
    if expected and not expected.intersection(actual):
        raise HTTPException(422, {'code': 'approved_design_hook_missing',
            'message': '构图未保留本次图片标题，创作记录已保留'})


async def _scene_treatment(board, plan, state, identifier):
    """Generate a visual scene around immutable source product pixels."""
    if not settings.image_api_key:
        return board
    from .image_service import ImageService, ImageServiceError
    from .design_image_tools import protection_mask, edit_mask, protect_regions, png_bytes, read_image
    from .poster import PosterLayer
    from PIL import Image
    if board.role in {'photo', 'detail', 'invitation'}:
        return board
    doc = board.document
    # Existing photographs already carry a complete scene. Extending a second
    # invented scene around their rectangular boundary creates visible seams.
    # Their natural route is measured crop/type; product cutouts use scene tools.
    if any(layer.role == 'photograph' for layer in doc.layers):
        return board
    products = [layer for layer in doc.layers if layer.type == 'image' and layer.visible and layer.role == 'product']
    if not products:
        return board
    image_state = state.setdefault('scene', {})
    previous = image_state.get(board.id)
    if previous and previous.get('status') == 'completed':
        data = previous['asset']
    elif previous and previous.get('status') in {'started', 'unavailable'}:
        return board  # never repeat an unknown paid effect after recovery
    else:
        image_state[board.id] = {'status': 'started'}
        _checkpoint(identifier, state)
        base = doc.model_copy(deep=True)
        base.layers = [layer for layer in base.layers if layer.type != 'text']
        original = await render_document(base)
        regions = [(int(layer.x), int(layer.y), min(doc.width, int(layer.x + layer.width + 1)),
                    min(doc.height, int(layer.y + layer.height + 1))) for layer in products]
        mask = protection_mask((doc.width, doc.height), regions)
        concept = next(c for c in plan.concepts if c.id == board.concept_id)
        prompt = ('Create a cohesive commercial editorial scene around the supplied product composition. '
                  'Preserve the protected product image regions exactly. Add meaningful lighting, texture and spatial depth '
                  'that support this visual concept. Keep space for typography. No text, logo, numbers, additional devices or people. '
                  'Visual concept: ' + concept.visual_thesis + '. Palette: ' + concept.background + ', ' + concept.accent)
        client = ImageService(settings.image_base_url, settings.image_api_key, settings.image_model, timeout=110)
        try:
            edited = await client.edit(prompt, original, mask=edit_mask(mask))
            candidate = read_image(edited.data)
            if candidate.size != (doc.width, doc.height):
                candidate = candidate.resize((doc.width, doc.height), Image.Resampling.LANCZOS)
            protected = protect_regions(original, png_bytes(candidate), mask)
            raw = protected['data']
            data = {'data_url': 'data:image/png;base64,' + base64.b64encode(raw).decode(),
                    'provenance': 'protected_source_composite', 'sha256': hashlib.sha256(raw).hexdigest(),
                    'protected_pixels_preserved': True, 'product_identity_verified': False,
                    'title': '本次视觉场景', 'request_id': edited.request_id, 'model': edited.model}
            image_state[board.id] = {'status': 'completed', 'asset': data}
        except ImageServiceError as exc:
            image_state[board.id] = {'status': 'unavailable', 'reason': exc.reason}
            _checkpoint(identifier, state)
            return board
        _checkpoint(identifier, state)
    asset_id = 'scene-' + board.id
    doc.assets[asset_id] = data
    doc.layers = [layer for layer in doc.layers if layer.type != 'shape']
    doc.layers.insert(0, PosterLayer(id=asset_id, type='image', name='视觉场景', x=0, y=0,
        width=doc.width, height=doc.height, z=-10, asset_id=asset_id, role='generated_scene', locked=True))
    return board


async def _commercial_treatment(board, plan, state, identifier, *, assets, page_index, task_data):
    """Compose the actual visual as a whole; never paste type over a finished design."""
    if not settings.image_api_key:
        return board
    from .design_art_direction import (create_commercial_artboard, review_commercial_artboard,
                                      DESIGN_REVIEW_PROTOCOL_VERSION)
    from .design.compositor import apply_full_frame_visual
    from .image_service import ImageService, ImageServiceError, ImageArtifact
    stages = state.setdefault('commercial_visuals', {})
    stage = stages.get(board.id, {})
    if stage.get('status') == 'started':
        raise HTTPException(409, '这次图片请求还未确认结果，请新建一次设计继续')
    if stage.get('status') == 'unavailable':
        raise HTTPException(503, '图片服务暂未完成这次设计，请稍后重新创作')
    if stage.get('status') != 'completed':
        stages[board.id] = {**stage, 'status': 'started'}
        _checkpoint(identifier, state)
        concept = next(item for item in plan.concepts if item.id == board.concept_id)
        brief = plan.brief.model_dump(mode='json')
        brief.update(source_task={key: task_data.get(key, '') for key in ('audience', 'scene', 'requirements')},
                     page_visual_brief=board.visual_brief)
        if stage.get('correction_checks'):
            # Keep actionable observations in their own bounded structure; a
            # serialized full review used to be cut in the middle of a string.
            brief['correction_checks'] = [{key: check[key] for key in
                ('category', 'severity', 'message', 'text_observations', 'unexpected_text',
                 'task_location', 'task_observation') if key in check}
                if isinstance(check, dict) else {'message': str(check)}
                for check in stage['correction_checks'][:4]
                if not isinstance(check, dict) or check.get('severity') in ('error', 'warning')]
            if stage.get('previous_asset'):
                brief['revision_image'] = stage['previous_asset']
        if page_index > 0:
            cover = next((item for item in stages.values() if item.get('page_index') == 0 and item.get('status') == 'completed'), None)
            if cover:
                brief['series_reference'] = cover['asset']
        texts = {key: value for key, value in brief['texts'].items() if key in board.source_text_ids}
        for source_id in list(texts):
            if source_id.startswith('design-copy-') and source_id.endswith('-headline'):
                caption_id = source_id.removesuffix('-headline') + '-caption'
                if caption_id in brief['texts']:
                    texts[caption_id] = brief['texts'][caption_id]
                    if caption_id not in board.source_text_ids:
                        board.source_text_ids.append(caption_id)
        for value in list(texts.values()):
            for required_id in value.get('required_text_ids', []):
                if required_id in brief['texts']:
                    texts[required_id] = brief['texts'][required_id]
                    if required_id not in board.source_text_ids:
                        board.source_text_ids.append(required_id)
        if board.role in ('cover', 'hero') and 'identity-product-label' in brief['texts']:
            texts['identity-product-label'] = brief['texts']['identity-product-label']
        client = ImageService(settings.image_base_url, settings.image_api_key, settings.image_model, timeout=180)
        try:
            result = await create_commercial_artboard(client, width=board.document.width,
                height=board.document.height, brief=brief, concept=concept.model_dump(mode='json'),
                role=board.role, page_index=page_index, texts=texts, assets=assets)
        except ImageServiceError as exc:
            stages[board.id] = {**stage, 'status': 'unavailable', 'reason': exc.reason, 'detail': exc.safe_message}
            _checkpoint(identifier, state)
            raise HTTPException(503, '图片服务暂未完成这次设计，请稍后重新创作') from None
        raw = result.artifact.data
        stage = {'status': 'completed', 'attempt': stage.get('attempt', 0) + 1, 'page_index': page_index, 'asset': {
            'data_url': 'data:' + result.artifact.mime + ';base64,' + base64.b64encode(raw).decode(),
            'title': board.title, 'sha256': hashlib.sha256(raw).hexdigest(),
            'provenance': result.provenance, 'width': result.artifact.width, 'height': result.artifact.height}}
        stages[board.id] = stage
        _checkpoint(identifier, state)
    actual_digest = hashlib.sha256(base64.b64decode(stage['asset']['data_url'].split(',', 1)[1])).hexdigest()
    cached_review = stage.get('reference_review') or {}
    current_review = (cached_review.get('protocol_version') == DESIGN_REVIEW_PROTOCOL_VERSION
                      and cached_review.get('reviewed_artifact_sha256') == actual_digest)
    if not cached_review.get('review_completed') or not current_review:
        if cached_review:
            stage['reference_review_history'] = [*stage.get('reference_review_history', []), cached_review][-8:]
        data = stage['asset']
        provenance = data['provenance']
        artifact = ImageArtifact(base64.b64decode(data['data_url'].split(',', 1)[1]),
            data['data_url'].split(';', 1)[0][5:], provenance['model'], provenance['request_id'],
            data['width'], data['height'], board.document.width, board.document.height)
        stage['reference_review'] = await review_commercial_artboard(artifact,
            base_url=settings.image_base_url, key=settings.image_api_key, model=settings.image_review_model,
            assets=assets, provenance=provenance)
        stage['review_attempts'] = stage.get('review_attempts', 0) + len(stage['reference_review'].get('review_attempts') or [None])
        _checkpoint(identifier, state)
    review = stage['reference_review']
    if (not review.get('review_completed')
            or review.get('protocol_version') != DESIGN_REVIEW_PROTOCOL_VERSION
            or review.get('reviewed_artifact_sha256') != actual_digest):
        raise HTTPException(503, '图片已保存，成图检查暂未完成，请稍后重试')
    reference_errors = [check for check in review.get('checks', []) if check.get('severity') == 'error']
    publication_only = (bool(reference_errors) and all(check.get('category') == 'publication' for check in reference_errors)
                        and not review.get('critical_uncertainties'))
    if (review.get('has_errors') or review.get('critical_uncertainties')) and not publication_only:
        if stage.get('attempt', 1) < 2:
            # A single explicit corrective pass, recorded before starting it.
            # Unknown paid requests are never automatically replayed.
            stages[board.id] = {'status': 'repair_pending', 'attempt': stage.get('attempt', 1),
                'previous_render_sha256': stage['asset']['sha256'],
                'previous_asset': stage['asset'],
                'correction_checks': review.get('checks', [])}
            _checkpoint(identifier, state)
            return await _commercial_treatment(board, plan, state, identifier,
                assets=assets, page_index=page_index, task_data=task_data)
        raise HTTPException(422, '这张图片还需要调整，创作记录已保留')
    board.document = apply_full_frame_visual(board.document, 'visual-' + board.id, stage['asset'])
    board.document.audit.append({'type': 'reference_comparison', **review})
    return board


async def execute_design(parameters):
    from .design import plan_design, compose_design, DesignPlan
    from .design_review import refine_design, review_with_model, review_design, compare_candidates, cached_design_renderer
    from .creative_agent_routes import model
    from .generation_jobs import generation_budget, commit_artifact_result
    from .design.source_integrity import RENDER_POLICY, native_source_proof
    identifier = parameters['design_id']
    render_current = cached_design_renderer(render_document)
    source, task = source_record(parameters['source_run_id'])
    parent = load_design(parameters['parent_id'], edit=True) if parameters.get('parent_id') else None
    craft_edit = parameters.get('craft_edit')
    if craft_edit is not None:
        from .design.craft_controls import CraftEditInput
        if not parent or parent['source_run_id'] != parameters['source_run_id']:
            raise HTTPException(422,{'code':'craft_edit_requires_parent','message':'请选择当前内容的图片再微调'})
        craft_edit = CraftEditInput.model_validate(craft_edit)
        if parameters['platform'] != parent['platform'] or parameters.get('surface','') != parent.get('surface',''):
            raise HTTPException(422,{'code':'craft_edit_cannot_change_surface','message':'局部微调保留原来的画幅'})
        try:
            parent_work = load_body('design_work', parent['id'])
        except HTTPException as exc:
            if exc.status_code != 404:
                raise
            parent_work = {}
        previous_copy = parent_work.get('design_copy') or {}
        if previous_copy.get('status') != 'approved' or not previous_copy.get('audit', {}).get('completed'):
            raise HTTPException(409, {'code':'craft_edit_parent_copy_unavailable',
                                     'message':'请先完成这张图片的文案，再微调排版'})
    try:
        state = load_body('design_work', identifier, action='write')
    except HTTPException as exc:
        if exc.status_code != 404:
            raise
        state = {'id': identifier, 'created_at': time.time(), 'source_run_id': parameters['source_run_id']}
    task_data = task.model_dump()
    task_data['platform'] = parameters['platform']
    task_data['design_surface'] = parameters.get('surface', '')
    task_data['design_instruction'] = parameters.get('instruction', '')
    record = deepcopy(source)
    design_llm_enabled = bool(settings.llm_api_key or (settings.design_text_model and settings.image_api_key))
    record['id'] = record['run_id'] = parameters['source_run_id']
    from .design_reference import design_references
    record['design_references'] = design_references(parameters['platform'])
    with generation_budget():
        if craft_edit is not None:
            from .design.craft_assets import collect_parent_assets
            try:
                assets = collect_parent_assets(parent)
            except ValueError as exc:
                raise HTTPException(409, {'code': str(exc), 'message':'请选择可编辑的原生图层版本继续微调'}) from None
            material_sources, subject_sources, brand_sources = [], [], []
            state.setdefault('asset_observations', deepcopy(parent_work.get('asset_observations', {})))
            state.setdefault('design_task', deepcopy(parent_work.get('design_task') or task.model_dump()))
            state.setdefault('design_copy', deepcopy(previous_copy))
            state['place_research_status'] = 'parent_revision_reused'
            # Reuse only plate reviews bound to their original byte hashes.
            # The revised composition still receives actual-render review.
            state.setdefault('element_pixel_reviews', deepcopy(parent_work.get('element_pixel_reviews', {})))
        else:
            assets = await collect_assets(parameters['source_run_id'], source)
            from .design.vmall_materials import collect_vmall_design_materials
            material_assets, material_sources = await collect_vmall_design_materials(source,task.product,assets)
            assets.update(material_assets)
            from .design.product_subjects import collect_product_subjects
            product_subjects, subject_sources = await collect_product_subjects(task.product, assets, max_subjects=8)
            assets.update(product_subjects)
            from .design_brand_materials import collect_brand_materials
            brand_assets, brand_sources = await collect_brand_materials(task.product, assets)
            assets.update(brand_assets)
        record['sources'] = list(record.get('sources') or [])
        known_sources = {item['id'] for item in record['sources']}
        for item in [*material_sources, *subject_sources, *brand_sources]:
            if item['id'] not in known_sources:
                record['sources'].append(item)
                known_sources.add(item['id'])
        if 'asset_observations' not in state:
            from .design_image_tools import describe_design_assets
            state['asset_observations'] = await describe_design_assets(assets, settings.image_base_url,
                settings.image_api_key, settings.image_review_model) if settings.image_api_key and settings.image_review_model else {}
            _checkpoint(identifier, state)
        record['asset_observations'] = state['asset_observations']
        for asset_id, observation in state['asset_observations'].items():
            if asset_id in assets and isinstance(observation, dict):
                assets[asset_id].update({key: value for key, value in observation.items() if key in ('view', 'framing', 'photo')})
        record['assets'] = [{'id': key, **value} for key, value in assets.items()]
        report('materials', f'素材 {len(assets)} 项 · 参考 {len(record["sources"])} 条')
        # Reuse existing source-bound research. Older generic runs may not have
        # it, so the agent researches the already selected store automatically.
        if 'design_task' not in state:
            from .store_task_research import prepare_store_task
            from .gtm_agent.place_context import PLACE_CONTEXT_MARKER
            from .llm import ModelCallError
            research_status = 'reused'
            if PLACE_CONTEXT_MARKER not in task.requirements:
                try:
                    task = await prepare_store_task(task)
                    research_status = 'completed'
                except HTTPException as exc:
                    if exc.status_code not in (422, 429, 502, 503, 504):
                        raise
                    research_status = 'unresolved'
                except (TimeoutError, ModelCallError):
                    research_status = 'unresolved'
            state['design_task'] = task.model_dump()
            state['place_research_status'] = research_status
            _checkpoint(identifier, state)
        task_data.update(state['design_task'])
        from .design_store_context import collect_design_store_context
        from .design_identity import resolve_design_identity
        if craft_edit is not None:
            context = deepcopy(parent['plan']['brief'].get('store_context', {}))
            identity = deepcopy(parent['plan']['brief'].get('brand_identity', {}))
        else:
            context = collect_design_store_context(task_data, record, assets=assets)
            identity = resolve_design_identity(task_data, record, assets,
                publisher_context={'publisher_identity': 'store',
                    'store_name': context.get('store', {}).get('name', '')})
        record['_store_context'] = context
        record['_brand_identity'] = identity
        from .design.craft import build_craft_contract
        from .design import get_platform_connector
        copy_craft_contract = build_craft_contract({**task_data,
            'platform':get_platform_connector(parameters['platform'], parameters.get('surface','')).model_dump(),
            'publisher_kind':identity.get('publisher_kind','store'), 'asset_ids':list(assets)}, record, assets)
        record['_craft_contract'] = copy_craft_contract
        context_signature = hashlib.sha256(json.dumps({'task': task_data, 'identity': identity,
            'store': context, 'facts': source.get('facts', [])}, ensure_ascii=False, sort_keys=True).encode()).hexdigest()
        if state.get('context_signature') not in (None, context_signature):
            if state.get('commercial_visuals') or state.get('boards'):
                raise HTTPException(409, '门店资料已更新，重新生成就能使用最新资料')
            for key in ('design_copy', 'plan', 'concept_selection', 'concept_reviews'):
                state.pop(key, None)
        state['context_signature'] = context_signature
        if 'design_copy' not in state:
            from .design_copy import prepare_design_copy
            # A visual refinement should not silently rewrite an approved title.
            changes_copy = bool(re.search(r'(?:改写|重写|换|修改).{0,8}(?:标题|文案|口号)|(?:标题|文案|口号).{0,8}(?:改成|改为|换成|重写|改写)', parameters.get('instruction', '')))
            # A newly commissioned concept can need a new line, whereas a
            # typography-only edit should retain the existing approved words.
            copy_instruction = parameters.get('instruction', '')
            if re.search(r'创意概念|重做创意|换个创意|更换选题|换个选题|重做海报|重新设计', copy_instruction):
                changes_copy = True
            if re.search(r'(?:保留|不改|不要改|保持).{0,6}(?:原文|标题|文案|口号)', copy_instruction):
                changes_copy = False
            if craft_edit is not None:
                changes_copy = False
            # A different publication package needs its own information design;
            # a previous title-only cover cannot stand in for a complete poster.
            if parent:
                from .design.publication import publication_strategy
                old_mode = (parent.get('plan', {}).get('brief', {}).get('publication') or {}).get('mode')
                if not old_mode:
                    old_mode = publication_strategy(parent.get('platform') or task.platform,
                        parent.get('surface', ''), '', source).mode
                new_mode = publication_strategy(parameters['platform'], parameters.get('surface', ''),
                    copy_instruction, source).mode
                if new_mode != old_mode:
                    changes_copy = True
            if parent and not changes_copy:
                try:
                    previous_work = load_body('design_work', parent['id'])
                except HTTPException as exc:
                    if exc.status_code != 404:
                        raise
                    previous_work = {}
                previous_copy = previous_work.get('design_copy') or {}
                old_evidence = {row.get('id') or row.get('source_id'): row.get('quote')
                    for row in parent.get('plan', {}).get('brief', {}).get('store_context', {}).get('evidence', [])}
                live_evidence = {row.get('id') or row.get('source_id'): row.get('quote') for row in context.get('evidence', [])}
                cited = {eid for card in previous_copy.get('cards', []) for eid in card.get('evidence_ids', [])}
                if previous_copy.get('status') == 'approved' and all(eid in live_evidence and old_evidence.get(eid) == live_evidence[eid] for eid in cited):
                    state['design_copy'] = deepcopy(previous_copy)
            from .design.publication import no_overlay_text_requested
            no_type = no_overlay_text_requested(parameters.get('instruction', ''))
            if 'design_copy' not in state:
                from .design import get_platform_connector
                state['design_copy'] = await prepare_design_copy(task_data, record,
                    brand_identity=identity, store_context=context, instruction=parameters.get('instruction', ''),
                    copy_craft_contract=copy_craft_contract,
                    max_pages=get_platform_connector(parameters['platform'], parameters.get('surface', '')).suggested_pages,
                    repair_rejections=True, auditor=_copy_auditor() if not no_type else None,
                    llm=_design_model(model, calls=6, tokens=3500) if design_llm_enabled and not no_type else None)
            _checkpoint(identifier, state)
        record['_design_copy'] = state['design_copy']
        report('copy', f'画面文案 {len(state["design_copy"].get("cards") or [])} 条'
               f' · {state["design_copy"].get("status", "已审定")}')
        record['assets'] = [{'id': key, **value} for key, value in assets.items()]
        # When a genuine prepared subject exists, whole historic campaign
        # artwork remains an art reference rather than another product cutout.
        has_subject = any(value.get('prepared_subject') for value in assets.values())
        photo_roles = {'store_photo', 'photo', 'scene', 'lifestyle', 'real_scene', 'user_photo'}
        record['images'] = [{'asset_id': key} for key, value in assets.items()
            if not has_subject or value.get('prepared_subject')
            or value.get('visual_role') in photo_roles
            or value.get('provenance') in {'user_photo', 'store_upload', 'authorized_photo'}]
        instruction = parameters.get('instruction', '')
        if parent:
            task_data['previous_design'] = parent.get('plan', {})
        if state.get('plan'):
            # A resumed draft may predate a stricter composition contract.
            # Keep its evidence available rather than surfacing a server error
            # or silently rendering a now-invalid layout.
            from pydantic import ValidationError
            try:
                plan = DesignPlan.model_validate(state['plan'])
            except ValidationError:
                _checkpoint(identifier, {**state, 'status': 'composition_needs_revision'})
                raise HTTPException(422, {'code': 'cached_composition_needs_revision',
                    'design_id': identifier, 'message': '这版构图需要重新创作，原需求与素材已保留'}) from None
        elif craft_edit is not None:
            from .design.craft_controls import apply_craft_controls
            parent_plan = DesignPlan.model_validate(parent['plan'])
            from .design.craft import build_craft_contract
            parent_contract = build_craft_contract(parent_plan.brief,record,assets)
            try:
                plan, order = apply_craft_controls(parent_plan,parent_contract,
                    page_id=craft_edit.page_id,region_id=craft_edit.region_id,
                    values=craft_edit.values,goal=craft_edit.goal)
            except ValueError:
                raise HTTPException(422,{'code':'craft_edit_out_of_scope','message':'这处调整需要保留原文案、素材和其他区域'}) from None
            state['plan'] = plan.model_dump(mode='json')
            state['craft_revision_order'] = order
            state['concept_selection'] = {'selected_id':plan.selected_concept_id,
                'reason':'只执行当前区域的制作参数微调','ranking':[]}
            _checkpoint(identifier,state)
        else:
            llm = _design_model(model, calls=2, tokens=4800) if design_llm_enabled else None
            from .design.planner import DesignInputNeeded
            try:
                plan = await plan_design(task_data, record, instruction=instruction,
                                         platform=parameters['platform'], llm=llm)
            except DesignInputNeeded as exc:
                raise HTTPException(422, str(exc)) from None
            state['plan'] = plan.model_dump(mode='json')
            _checkpoint(identifier, state)
        report('concepts', f'{len(plan.concepts)} 个视觉方向')
        if design_llm_enabled and plan.provenance.get('fallback_used'):
            _checkpoint(identifier, {**state, 'status': 'art_direction_incomplete'})
            raise HTTPException(503, {'code': 'art_direction_incomplete', 'design_id': identifier,
                'message': '艺术指导尚未完成，创作需求已保留'})
        # Compare actual cover renders before committing the series' art direction.
        # A missing visual model gives geometry checks, never a made-up taste score.
        constraints = plan.provenance.get('revision_constraints', {})
        page_changes = plan.provenance.get('explicit_page_assignments', {})
        reuse_cover = bool(parent and craft_edit is None and 'composition' in constraints.get('locked_fields', [])
                          and '1' not in page_changes and parent.get('artboards')
                          and parent.get('render_policy') == RENDER_POLICY)
        if reuse_cover and not state.get('concept_selection'):
            state['concept_selection'] = {'selected_id': plan.selected_concept_id,
                'reason': '局部素材修改保留已选封面', 'ranking': []}
            _checkpoint(identifier, state)
        # Image credentials enable actual-render review, never a silent switch
        # from authorized source composition to regenerated product photography.
        state['render_policy'] = RENDER_POLICY
        if not (state.get('concept_selection') or {}).get('selected_id'):
            candidates = []
            measured = state.setdefault('concept_reviews', {})
            async def inspect_concept(concept):
                candidate_plan = plan.model_copy(update={'selected_concept_id': concept.id})
                candidate = compose_design(task_data, record, candidate_plan, assets)[0]
                if (concept.id in measured
                        and measured[concept.id].get('execution_id') == candidate.id):
                    return {'id': concept.id, 'review': measured[concept.id]}
                if concept.id in measured:
                    state.setdefault('legacy_concept_review_history', []).append(
                        {'concept_id': concept.id, 'review': measured.pop(concept.id)})
                _validate_delivered_copy(candidate.document, state.get('design_copy', {}), instruction=instruction)
                from .design_review import audit_design_document
                compile_checks = audit_design_document(candidate.document)
                compile_errors = sum(row.get('severity') == 'error' for row in compile_checks)
                if compile_errors:
                    review = {'checks': compile_checks, 'errors': compile_errors,
                        'warnings': sum(row.get('severity') == 'warning' for row in compile_checks),
                        'score': None, 'deterministic_completed': False, 'review_completed': False,
                        'status': 'layout_needs_revision', 'edits': [], 'execution_id': candidate.id}
                    measured[concept.id] = review
                    _checkpoint(identifier, state)
                    return {'id': concept.id, 'review': review}
                try:
                    candidate.document = await _design_elements_treatment(
                        identifier, state, candidate_plan, candidate, assets)
                except HTTPException as exc:
                    code = exc.detail.get('code') if isinstance(exc.detail, dict) else None
                    if code not in {'design_elements_rejected', 'design_elements_review_incomplete',
                                    'design_elements_incomplete', 'design_elements_attempt_incomplete'}:
                        raise
                    element_review = state.get('design_elements', {}).get(candidate.id, {}).get('pixel_review') or {}
                    checks = [{'category': 'imagery', 'severity': 'error',
                        'message': code, 'element_observations': element_review.get('checks', [])}]
                    review = {'checks': checks, 'errors': 1, 'warnings': 0, 'score': None,
                        'deterministic_completed': False, 'review_completed': False,
                        'status': code, 'edits': [], 'execution_id': candidate.id}
                    measured[concept.id] = review
                    _checkpoint(identifier, state)
                    return {'id': concept.id, 'review': review}
                native_source_proof(candidate.document, assets)
                async def compare_reviewer(png, doc):
                    return await review_with_model(png, doc, settings.image_base_url,
                        settings.image_api_key, _design_review_model(),
                        design_context={'platform': parameters['platform'], 'concept': concept.visual_thesis,
                            'publication': plan.brief.publication.model_dump() if plan.brief.publication else {},
                            'art_direction': concept.art_direction.model_dump(mode='json'),
                            'visual_devices': [item.model_dump(mode='json') for item in concept.visual_devices],
                            'instruction': instruction,
                            'generated_elements_scope': 'abstract_design_not_documentary_scene_or_camera_samples',
                            'source_reference_images': [value['data_url'] for value in candidate.document.assets.values()
                                if isinstance(value.get('data_url'), str)
                                and value.get('provenance') != 'ai_generated_design_elements'][:3]})
                refined_candidate = await refine_design(candidate.document, render_current,
                    reviewer=compare_reviewer if settings.image_api_key and _design_review_model() else None,
                    max_rounds=2)
                candidate.document = refined_candidate['document']
                native_source_proof(candidate.document, assets)
                _validate_delivered_copy(candidate.document, state.get('design_copy', {}), instruction=instruction)
                review = refined_candidate['review']
                review['execution_id'] = candidate.id
                state.setdefault('candidate_boards', {})[concept.id] = candidate.model_dump(mode='json')
                measured[concept.id] = review
                _checkpoint(identifier, state)
                return {'id': concept.id, 'review': review}
            # Candidate preparation shares checkpointed image work. Keep its
            # mutations serial; the explicit visual role and exact-pixel render
            # reuse reduce latency without overlapping writes or image effects.
            candidates = [await inspect_concept(concept) for concept in plan.concepts[:2]]
            selection = compare_candidates(candidates)
            if not selection['selected_id']:
                if state.get('art_direction_replans', 0) < 1 and design_llm_enabled:
                    feedback = [{'concept_id': row['id'],
                        'render_sha256': row['review'].get('render_sha256', ''),
                        'checks': [check for check in row['review'].get('checks', [])
                                     if check.get('severity') in ('error', 'warning')][:6]}
                                for row in candidates]
                    state['art_direction_replans'] = 1
                    state['art_direction_feedback'] = feedback
                    state.setdefault('art_direction_history', []).append({
                        'plan': plan.model_dump(mode='json'), 'selection': selection,
                        'reviews': deepcopy(measured)})
                    _checkpoint(identifier, state)
                    repair_task = {**task_data, 'design_feedback': feedback}
                    repair_instruction = (instruction + '\n后台实际排版与成图反馈：' +
                        json.dumps(feedback, ensure_ascii=False)[:6000] +
                        '\n只修已定位问题，保留本次审定标题与原任务创意。调整布局和视觉机制，不以缩小字或退回纯色模板换取通过。')
                    repaired = await plan_design(repair_task, record, instruction=repair_instruction,
                        platform=parameters['platform'], llm=_design_model(model, calls=2, tokens=4800))
                    state.update(plan=repaired.model_dump(mode='json'), concept_reviews={},
                                 candidate_boards={}, boards={})
                    state.pop('concept_selection', None)
                    _checkpoint(identifier, state)
                    return await execute_design(parameters)
                state['concept_selection'] = selection
                _checkpoint(identifier, {**state, 'status': 'commercial_direction_needs_revision'})
                raise HTTPException(422, {'code': 'commercial_direction_needs_revision', 'design_id': identifier})
            plan.selected_concept_id = selection['selected_id']
            state['concept_selection'] = selection
            state['plan'] = plan.model_dump(mode='json')
            _checkpoint(identifier, state)
        boards = compose_design(task_data, record, plan, assets)
        if craft_edit is not None:
            from .design.craft_assets import preserve_parent_document
            baseline_boards = compose_design(task_data, record, parent['plan'], assets)
            for board in boards:
                baseline = next((item for item in baseline_boards if item.page_id == board.page_id
                                 and item.concept_id == board.concept_id), None)
                if baseline is None:
                    raise HTTPException(409, {'code':'craft_edit_parent_page_unavailable'})
                try:
                    board.document = preserve_parent_document(parent,board,baseline,craft_edit)
                except ValueError as exc:
                    raise HTTPException(409, {'code':str(exc), 'message':'这份旧稿需要先恢复区域对应关系'}) from None
        # Completed boards contain generated design plates as well as source
        # subjects. Restore only checkpointed plates before validating resume.
        for entry in state.get('design_elements', {}).values():
            if entry.get('status') == 'ready' and isinstance(entry.get('asset'), dict):
                metadata = entry['asset']
                assets[metadata['id']] = metadata
        completed = state.setdefault('boards', {})
        output = []
        marketing_context = _visual_marketing_context(source, task_data, context, identity)
        for index, board in enumerate(boards):
            report('rendering', f'第 {index + 1} 张，共 {len(boards)} 张')
            if index == 0:
                selected_candidate = state.get('candidate_boards', {}).get(plan.selected_concept_id)
                if selected_candidate and selected_candidate.get('id') == board.id:
                    from .poster import PosterDocument
                    board.document = PosterDocument.model_validate(selected_candidate['document'])
            existing = completed.get(board.id)
            if existing and existing.get('source_integrity', {}).get('render_policy') != RENDER_POLICY:
                # Keep legacy work as history; it is not a native-source result.
                state.setdefault('legacy_board_history', {})[board.id] = deepcopy(existing)
                existing = None
            if existing:
                native_source_proof(existing['document'], assets)
                await _audit_embedded_elements(identifier, state, existing['document'])
            if (existing and existing.get('review', {}).get('review_completed')
                    and existing.get('review', {}).get('marketing_action') == 'deliver'
                    and existing.get('review', {}).get('publication_gate', {}).get('action') != 'revise'
                    and existing.get('render_sha256') == existing.get('review', {}).get('marketing_review', {}).get('render_sha256')):
                output.append(completed[board.id])
                continue
            if index == 0 and reuse_cover:
                item = deepcopy(parent['artboards'][0])
                # Reuse the actual visual, but review it against the current copy
                # and publisher context rather than inheriting an old verdict.
                from .poster import PosterDocument
                board.document = PosterDocument.model_validate(item['document'])
            elif existing:
                from .poster import PosterDocument
                board.document = PosterDocument.model_validate(existing['document'])
            board.render_mode = 'native'
            _validate_delivered_copy(board.document, state.get('design_copy', {}), instruction=instruction)
            if craft_edit is not None:
                from .design.craft_assets import restore_parent_elements
                board.document = restore_parent_elements(parent, board)
                await _audit_embedded_elements(identifier, state, board.document)
            else:
                board.document = await _design_elements_treatment(identifier, state, plan, board, assets)
            native_source_proof(board.document, assets)
            reviewer = None
            if settings.image_api_key and _design_review_model():
                async def reviewer(png, doc):
                    return await review_with_model(png, doc, settings.image_base_url,
                        settings.image_api_key, _design_review_model(),
                        design_context={'platform': parameters['platform'], 'role': board.role,
                            'publication': plan.brief.publication.model_dump() if plan.brief.publication else {},
                            'instruction': instruction, 'revision_constraints': task_data.get('previous_design', {}),
                                'marketing_context': marketing_context,
                                'image_copy': [item['text'] for meta in doc.assets.values()
                                    for item in (meta.get('provenance', {}).get('display_copy', [])
                                        if isinstance(meta.get('provenance'), dict) else []) if isinstance(item.get('text'), str)],
                                'source_reference_images': [value['data_url'] for value in doc.assets.values()
                                    if isinstance(value.get('data_url'), str)
                                    and value.get('provenance') != 'ai_generated_design_elements'][:3],
                                'generated_design_elements': [
                                    {'id': key, 'purpose': 'abstract_design_only_not_scene_or_product_evidence'}
                                    for key, value in doc.assets.items()
                                    if value.get('provenance') == 'ai_generated_design_elements']})
            refined = await refine_design(board.document, render_current, reviewer=reviewer,
                                          max_rounds=0 if craft_edit is not None else 2)
            board.document = refined['document']
            _validate_delivered_copy(board.document, state.get('design_copy', {}), instruction=instruction)
            item = board.model_dump(mode='json')
            item['source_integrity'] = native_source_proof(board.document, assets)
            if board.id in state.get('source_preservation', {}):
                item['source_preservation'] = deepcopy(state['source_preservation'][board.id])
            if board.id in state.get('native_design_composition', {}):
                item['production_route'] = deepcopy(state['native_design_composition'][board.id])
            item['review'] = refined['review']
            publication_findings = [check for entry in board.document.audit
                if entry.get('type') == 'reference_comparison' for check in entry.get('checks', [])
                if check.get('category') == 'publication' and check.get('severity') == 'error']
            if publication_findings:
                # Preserve the independent marketing verdict. The separate
                # publication gate still requires a real editorial correction.
                item['review']['publication_gate'] = {'action': 'revise',
                    'feedback': ['；'.join(str(check[key]) for key in
                        ('message', 'task_location', 'task_observation') if check.get(key))
                        for check in publication_findings]}
            item['review_history'] = refined['reviews']
            item['render_sha256'] = hashlib.sha256(refined['png']).hexdigest()
            completed[board.id] = item
            output.append(item)
            _checkpoint(identifier, state)
        ensure_live_context()
        source_record(parameters['source_run_id'])
        report('reviewing', f'成品口径与营销复核 {len(output)} 张')
        for item in output:
            review = item.get('review', {})
            action = review.get('publication_gate', {}).get('action') or review.get('marketing_action', 'incomplete')
            if not review.get('review_completed') or action == 'incomplete':
                _checkpoint(identifier, {**state, 'status': 'review_incomplete'})
                raise HTTPException(503, {'code': 'review_incomplete', 'design_id': identifier,
                    'message': '图片已保存，成品审阅尚未完成'})
            if action in ('reselect', 'revise'):
                if action == 'revise' and craft_edit is None and not state.get('marketing_revision_count') and design_llm_enabled:
                    # Complete the editorial loop for the salesperson. New copy
                    # is independently checked, then the existing artwork is
                    # edited once; the original source and work remain intact.
                    state['marketing_revision_count'] = 1
                    state['marketing_feedback'] = [*review.get('publication_gate', {}).get('feedback', []),
                        *review.get('marketing_review', {}).get('feedback', [])]
                    _checkpoint(identifier, state)
                    from .design_copy import prepare_design_copy
                    correction = (parameters.get('instruction', '') + '\n本次图片的独立编辑反馈：' +
                        json.dumps(state['marketing_feedback'], ensure_ascii=False) +
                        '\n保持本次外观/体验选题与真实产品，写出更具体的顾客观察任务和自然下一步；不添加参数或服务承诺来凑卖点。')
                    repaired_copy = await prepare_design_copy(task_data, record,
                        brand_identity=identity, store_context=context, instruction=correction,
                        max_pages=plan.brief.platform.suggested_pages, repair_rejections=True,
                        auditor=_copy_auditor(), llm=_design_model(model, calls=6, tokens=2200))
                    state['marketing_repair_copy'] = repaired_copy
                    _checkpoint(identifier, state)
                    if repaired_copy.get('status') == 'approved':
                        record['_design_copy'] = repaired_copy
                        repair_task = {**task_data, 'previous_design': plan.model_dump(mode='json')}
                        repaired_plan = await plan_design(repair_task, record, instruction=correction,
                            platform=parameters['platform'], llm=_design_model(model, calls=2, tokens=4800))
                        repaired_boards = compose_design(repair_task, record, repaired_plan, assets)
                        for board_index, repaired_board in enumerate(repaired_boards):
                            prior = output[min(board_index, len(output) - 1)]
                            prior_visual = next((value for value in prior['document'].get('assets', {}).values()
                                if isinstance(value.get('provenance'), dict) and value.get('data_url')), None)
                            if prior_visual:
                                state.setdefault('commercial_visuals', {})[repaired_board.id] = {
                                    'status': 'repair_pending', 'attempt': 1, 'previous_asset': prior_visual,
                                    'correction_checks': state['marketing_feedback']}
                        state.update(design_copy=repaired_copy, plan=repaired_plan.model_dump(mode='json'), boards={},
                            concept_selection={'selected_id': repaired_plan.selected_concept_id,
                                'reason': '沿用品牌与门店语境，执行一次独立编辑修订', 'ranking': []})
                        _checkpoint(identifier, state)
                        return await execute_design(parameters)
                _checkpoint(identifier, {**state, 'status': 'marketing_' + action})
                raise HTTPException(422, {'code': 'marketing_' + action, 'design_id': identifier,
                    'source_run_id': parameters['source_run_id'],
                    'feedback': review.get('marketing_review', {}).get('feedback', []),
                    'render_sha256': item.get('render_sha256')})
        status = 'needs_revision' if any(b['review']['errors'] for b in output) else 'ready'
        artifact = {'id': identifier, 'title': _delivered_title(source, output),
            'render_policy': RENDER_POLICY,
            'version': (parent['version'] + 1) if parent else 1, 'status': status,
            'platform': parameters['platform'], 'surface': plan.brief.platform.surface,
            'source_run_id': parameters['source_run_id'],
            'parent_id': parameters.get('parent_id'), 'created_at': time.time(),
            'plan': plan.model_dump(mode='json'), 'selection': state.get('concept_selection'), 'artboards': output}
        if state.get('craft_revision_order'):
            artifact['craft_revision'] = state['craft_revision_order'].get('summary',{})
        if status != 'ready':
            # Preserve the actual failed work for an explicit retry, not a success projection.
            _checkpoint(identifier, {**state, 'status': status})
            raise HTTPException(422, '这组图片还需要调整排版，原内容已保留')
        response = project_design(artifact)
        commit_artifact_result('design', identifier, artifact, response)
        return JSONResponse(response)


def artboard(identifier, board_id):
    from .poster import PosterDocument
    design = load_design(identifier)
    board = next((item for item in design['artboards'] if item['id'] == board_id), None)
    if board is None:
        raise HTTPException(404, '没有找到这张图片')
    return PosterDocument.model_validate(board['document'])
