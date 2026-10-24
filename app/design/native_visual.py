"""Task-led native visual direction, with source scope separated from aesthetic intent.

This is a planning contract, not a classifier, authenticity score or pixel proof.
Only existing authorized IDs describe photos; prose cannot invent a new source.
"""
from __future__ import annotations

import re
import hashlib
import json
from .publication import no_overlay_text_requested

VERSION = 'native-visual.v1'


def visual_strategy_contract():
    """Equivalent creative routes; source evidence and current purpose choose, not account stereotypes."""
    return {
        'version': 'visual-strategy.v1',
        'routes': {
            'product_hero': '准确产品本体、色彩、多角度或阵列与精心字体/留白就是主视觉；不需要另加生活道具或背景故事。',
            'ksp_explanation': '真实功能细节和批准卖点形成清楚的信息层级；示意不是相机实拍、性能测试或新能力证据。',
            'verbal_visual_turn': '已审短句的双关与真实产品视角、图形或字形发生可见关系，不强制画成生活场景。',
            'emotional_campaign': '情绪企划可以由色彩、尺度、留白和字体表达，也可选择有职责的原创生活静物。',
            'editorial_narrative': '确有叙事职责时才用物件、空间和图文关系讲清本次故事；来源照片与原创设计不得混称。',
        },
        'selection_rule': '依据本次传播目标、实际文案与准确可用素材选择，不按品牌/门店身份或平台一刀切；这些路线同等合法，没有默认生活化路线。',
        'native_only_valid': True,
        'generated_elements': 'optional_only_when_they_have_an_unmet_visible_job',
        'native_only_condition': '准确产品层、批准字体、留白、原生图形和必要光影已经完成视觉命题时，原生构图本身就是完整设计，不为证明创作强加生成背景。',
        'identity_boundary': '产品阵列只能使用准确授权的真实视角/配色；缺失背面、型号或颜色不得通过生成或跨代借图补齐。',
    }


def content_visual_commission(brief, source=None):
    """Carry the actual post's meaning across art planning and element direction.

    These strings are semantic input, never newly authorized overlay copy or
    factual evidence. Whole units are selected rather than slicing conditions.
    """
    brief = brief if isinstance(brief, dict) else brief.model_dump() if hasattr(brief, 'model_dump') else {}
    source = source if isinstance(source, dict) else {}
    source = source.get('result') if isinstance(source.get('result'), dict) else source
    publication = brief.get('publication') or {}
    publication = publication if isinstance(publication, dict) else {}
    companion = publication.get('companion_copy') or {}
    companion = companion if isinstance(companion, dict) else {}
    raw = [('title', source.get('title') or companion.get('title')),
           ('intro', source.get('intro')), ('body', source.get('body') or (companion.get('body') if not source.get('blocks') else ''))]
    raw += [(f'body:{index}', row.get('text')) for index, row in enumerate(source.get('blocks', [])[:12]) if isinstance(row, dict)]
    raw.append(('cta', source.get('cta')))
    units, used, seen, omitted = [], 0, set(), 0
    for region, value in raw:
        if not isinstance(value, str) or not value.strip() or value.strip() in seen:
            continue
        text = value.strip()
        if len(text) > 1800 or used+len(text) > 3600:
            omitted += 1
            continue
        units.append({'region': region, 'text': text})
        used += len(text)
        seen.add(text)
    instruction = _text(brief.get('instruction'), 1500)
    source_only = bool(re.search(r'仅做原生取景|不生成新的背景或装饰|不要重新设计|只(?:做|进行)(?:原图|照片)裁切', instruction))
    task = _text(brief.get('scene'))
    value = {
        'version': 'content-visual-commission.v1', 'active': bool(units or task),
        'source_run_id': str(brief.get('source_run_id') or source.get('run_id') or source.get('id') or '')[:120],
        'platform': _platform(brief.get('platform')), 'publication_mode': publication.get('mode', ''),
        'post_meaning': units, 'omitted_whole_units': omitted,
        'editorial_task': task, 'image_job': _text(publication.get('image_job'), 500),
        'caption_job': _text(publication.get('body_job'), 500),
        'production_intent': 'source_arrangement' if source_only else 'content_led_design',
        'visual_strategy': visual_strategy_contract(),
        'overlay_policy': 'no_overlay_text' if no_overlay_text_requested(instruction) else 'approved_copy_only',
        'translation_task': '先从实际正文提炼本次可见命题，再由准确产品层、观看尺度、原生图形、图文关系和有职责的原创设计元素共同兑现；不是把整段正文绘成字，也不是换地名后贴一张官方图。',
        'image_caption_relation': '图片承担第一眼能看见的关系，正文补充生活动机；不必把每句话照成实景，但换成另一篇正文后不应还只有同一泛用壁纸。',
        'source_role': 'official_or_uploaded_product_assets_are_immutable_subject_material_not_the_finished_design',
        'semantic_input_only': True,
        'boundaries': '不把正文中的隐喻、情绪或场景设想当作新增产品能力、顾客亲历或真实门店设施；图上文字只取原批准copy编号，产品只取授权原像素。',
    }
    value['sha256'] = hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True).encode()).hexdigest()
    return value


def _text(value, limit=700):
    return re.sub(r'\s+', ' ', value).strip()[:limit] if isinstance(value, str) else ''


def _platform(value):
    if not isinstance(value, str):
        value = (value.get('platform') or value.get('name') or '') if isinstance(value, dict) else ''
    return {'moments': '朋友圈', 'xiaohongshu': '小红书', 'wechat': '微信公众号'}.get(value, value)


def native_visual_contract(brief, *, role='', assets=None):
    """Build source-bound options; explicit brief determines the selected route.

    ``assets`` are supplied by the existing authorized design pipeline. Store
    photo IDs must also occur in the authorized store context; a made-up label
    such as 'my shop' never grants local-scene authority.
    """
    brief = brief if isinstance(brief, dict) else brief.model_dump() if hasattr(brief, 'model_dump') else {}
    store = brief.get('store_context') or {}
    store = store if isinstance(store, dict) else {}
    assets = assets if isinstance(assets, dict) else {}
    declared = store.get('photo_asset_ids') or []
    declared = declared[:32] if isinstance(declared, list) else []
    photos = [identifier for identifier in declared if isinstance(identifier, str) and identifier in assets]
    task = brief.get('source_task') or {}
    task = task if isinstance(task, dict) else {}
    instruction = _text(brief.get('instruction'), 1100)
    scene = _text(store.get('reader_task') or task.get('scene') or brief.get('scene'))
    platform = _platform(brief.get('platform'))
    explicit_editorial = bool(re.search(r'主视觉|品牌大片|概念海报|广告海报|杂志封面|重新设计|重新构图|视觉大片|定制配图|原创设计|定制视觉|重新制作|围绕.{0,30}(?:文案|正文|内容).{0,20}(?:设计|制作|定制)', instruction))
    explicit_observation = bool(re.search(r'保留.{0,10}(?:原图|照片|现场)|(?:原图|照片|现场).{0,10}(?:保持|不变)|不要重画|不要换背景|只.{0,8}(?:裁切|调光|调色)|随手拍|现场照', instruction))
    if explicit_editorial:
        selected = 'editorial_concept'
    elif explicit_observation and photos:
        selected = 'source_observation'
    elif role in {'detail', 'proof', 'explanation'}:
        selected = 'detail_annotation' if role == 'detail' else 'choice_explanation'
    elif photos and (role == 'photo' or platform == '朋友圈'):
        selected = 'source_observation'
    elif platform in {'朋友圈', '小红书'} and role in {'photo', 'cover', ''}:
        selected = 'product_observation'
    else:
        selected = 'editorial_concept'
    return {
        'version': VERSION, 'selected': selected, 'platform': platform,
        'selection_basis': 'explicit_request_and_available_material_not_publisher_stereotype',
        'reader_task': scene, 'authorized_photo_ids': photos[:4],
        'visual_strategy': visual_strategy_contract(),
        'scene_authority': 'visible_source_photo_only' if photos else 'designed_scene_not_store_evidence',
        'observation_task': '选择一个本页真正值得看的细节、物件关系或观看动作；由当前任务决定，不额外编造发生过的故事。',
        'source_preservation': '真实照片的主体、背景物件、光线方向、可见空间关系优先保留；只改变本次明确要求的部分。',
        'camera_language': '取景距离、主体尺度、自然透视和一处观察重点服务任务；不默认棚拍、微距、广角或浅景深。',
        'page_relationship': '首图可只提出观察，后页再解释；不要每页重复同一大标题、同一产品摆台和同一CTA。',
        'framing_policy': '朋友圈无叠字单原图优先沿用原照片比例；缩略图中产品与关键动作应可辨。大面积色带须承担实际信息职责，不为统一方图机械装裱。明确指定画幅、装裱、多图或信息图任务保留其编排。',
        'text_approach': '可无叠字；需要文字时仅用已批准短文案补充画面没有说出的信息，不用整页条目把图变成说明书。',
        'authenticity_scope': '生成/重绘仍是设计稿，不成为实拍、手机成像样张、真实顾客经历或本店现场的证据。',
        'avoid_shortcuts': ['伪造平台截图/评论/点赞/时间戳', '用糊图或随机杂乱代替真实观察',
                           '虚构校名地标门头或顾客', '为贴近生活改变产品外观与配色'],
        'pixel_preservation_verified': False,
    }


NATIVE_VISUAL_INSTRUCTION = (
    '按native_visual_contract选择这一次的观看方式，不把所有内容做成品牌KV，也不把所有门店图做成假UGC。'
    'source_observation保留真实来源中的空间、物件关系和自然光线；product_observation在准确产品视角中找具体发现；'
    'detail_annotation让读者看清同一个真实细节，choice_explanation让真实依据与判断动作形成关系；'
    'editorial_concept可以有鲜明的字体、构图和视觉张力，但仍为设计表达。'
    '产品主视觉、KSP解释、双关、情绪企划、编辑叙事按本次目的平等选择；准确产品阵列与精心字体/留白本身可以完成设计，不必每次生活化、加道具或生成背景。'
    '原生感不等于低清或没设计；不要人为加假手机截图框、滤镜破损、虚构随手拍时间或点赞评论。'
    '画面的动作、视角、物件和文字共同回答一个问题；好看但无关的石台、植物、光斑不替代本页观察。'
    '当需要保留实拍时，不为了商业精修重建全场景；没有实拍时，不把设计里的普通空间写成真实门店。'
)


def apply_native_photo_canvas(brief, concept, assets):
    """A narrow single-source default; no source pixels or identity rules change."""
    if brief.platform.platform != '朋友圈' or not no_overlay_text_requested(brief.instruction):
        return None
    if re.search(r'正方形|方图|1[:：]1|装裱|(?:保留|保持).{0,5}(?:画幅|画布|边框)', brief.instruction):
        return None
    if len(concept.pages) != 1 or len(concept.pages[0].composition) != 1:
        return None
    region = concept.pages[0].composition[0]
    if region.role != 'image' or region.rotation or region.shadow_opacity:
        return None
    identifier = region.asset_id or concept.pages[0].asset_id
    source = assets.get(identifier, {})
    width, height = source.get('width'), source.get('height')
    if (not isinstance(width, int) or isinstance(width, bool) or not isinstance(height, int)
            or isinstance(height, bool) or min(width, height) < 32 or width*height > 40_000_000):
        return None
    longest = max(brief.platform.width, brief.platform.height)
    new_width, new_height = round(longest*width/max(width,height)), round(longest*height/max(width,height))
    if min(new_width,new_height) < 320:
        return None  # Very long banners need a deliberate format, not a tiny thumbnail.
    previous = [brief.platform.width, brief.platform.height]
    brief.platform = brief.platform.model_copy(update={'width':new_width, 'height':new_height})
    region.x=region.y=0
    region.width=region.height=1
    return {'method':'single_source_native_aspect', 'asset_id':identifier,
            'source_size':[width,height], 'previous_canvas':previous,
            'canvas':[new_width,new_height], 'source_pixels_changed':False,
            'scope':'Layout aspect only; source bytes and downstream source/pixel checks remain unchanged.'}
