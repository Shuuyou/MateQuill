"""Editorial planning and metadata-based image checks, never pixel verification."""
from __future__ import annotations

import re
from .models import AuditItem, ImagePlacement, ContentBlock


TOPICS = {
    'display': ('屏幕', '显示', '柔光', 'oled', '阅读'),
    'port': ('接口', 'usb', '投影', '扩展坞'),
    'pen': ('手写', '画画', '创作', '触控'),
    'collaboration': ('协同', '接续', '传文件', '互联', '剪贴板'),
    'mobility': ('轻薄', '重量', '携带', '差旅', '通勤'),
    'performance': ('性能', '处理器', '内存', '剪辑', '游戏'),
}


def topics(text):
    text = text.casefold()
    return {key for key, words in TOPICS.items() if any(w in text for w in words)}


def bind_legacy_images(images, block_count):
    """Old positions are only accepted when they state an actual location."""
    bound = []
    for image in images:
        item = image.model_copy(deep=True)
        if item.block_index is None and item.role != 'cover':
            if re.search('封面|头图|主视觉', item.position):
                item.role = 'cover'
            else:
                match = re.search(r'第\s*(\d+)\s*段', item.position)
                if match:
                    item.block_index = int(match[1]) - 1
                elif item.position in ('正文后', '正文末尾') and block_count:
                    item.block_index = block_count - 1
        bound.append(item)
    return bound


def image_editorial_audit(result, bundle):
    issues = []
    by_id = {a.id: a for a in bundle.assets}
    facts = {f.id: f for f in bundle.facts}
    def add(severity, message):
        issues.append(AuditItem(severity=severity, category='image_relevance', message=message))
    for item in result.images:
        asset = by_id.get(item.asset_id)
        if not asset:
            continue  # Provenance audit handles missing IDs.
        if item.block_index is not None and item.block_index >= len(result.blocks):
            add('error', '图片绑定了不存在的正文段落，不能自动堆到文末')
            continue
        if item.role != 'cover' and item.block_index is None:
            add('error', '非封面图片没有明确关联段落，需要重新选图或定位')
        if item.role != 'cover' and asset.visual_role == 'product_overview':
            add('error', '普通外观图只作封面或产品总览，不能随意插入功能论述段落')
        if any(fid not in facts for fid in item.supported_fact_ids):
            add('error', '图片关联了不存在的事实 ID')
        if item.block_index is not None and item.block_index < len(result.blocks):
            block = result.blocks[item.block_index]
            if any(fid not in block.fact_ids for fid in item.supported_fact_ids):
                add('error', '图片事实 ID 不属于绑定的正文段落')
            image_topics = topics(asset.title)
            block_topics = topics(block.text)
            contextual_scene = item.role=='context' and asset.visual_role=='scene' and not item.supported_fact_ids and bool(item.reason.strip())
            if image_topics and block_topics and not image_topics.intersection(block_topics) and not contextual_scene:
                add('error', f'素材“{asset.title}”与关联段落主题不符，需要调整选图')
            if asset.visual_role in ('product_overview', 'unknown') and item.supported_fact_ids:
                add('error', '外观或未知画面的图片不能作为产品功能或参数的证明')
        if not item.reason.strip():
            add('warning', f'素材“{asset.title}”缺少选图理由，需编辑确认用途')
        if re.search('亲测|实拍|课堂现场|客户现场|实际使用效果', item.caption):
            if not re.search('亲测|实拍|课堂现场|客户现场|实际使用效果', asset.title):
                add('error', '配图说明把产品素材包装成未经证实的真实使用现场')
    if result.images:
        if any(by_id.get(i.asset_id) and by_id[i.asset_id].semantic_basis!='pixel_review_sha256' for i in result.images):
            add('warning', '部分配图仅依据素材标题；未经像素审阅，发布前必须确认画面')
        else:
            add('info', '所选图片与已审阅像素的 SHA256 一致；视觉观察不认证参数、型号或素材授权')
    return issues


def choose_preview_images(task, bundle, blocks):
    """No quota filling: one honest cover plus distinct, topic-matched details."""
    available = list(bundle.assets)
    if not available:
        return []
    cover = next((a for a in available if a.visual_role == 'product_overview'), available[0])
    chosen = [ImagePlacement(asset_id=cover.id, caption=cover.title or '产品素材',
        position='封面', role='cover', reason='展示所选型号；不作为实际使用效果或参数证明')]
    if task.platform == 'poster':
        return chosen
    limit = {'moments': 3, 'wechat': 4, 'xiaohongshu': 6}[task.platform]
    for index, block in enumerate(blocks):
        matched = next((a for a in available if a.id not in {x.asset_id for x in chosen}
            and a.visual_role in ('feature_detail', 'scene')
            and topics(a.title).intersection(topics(block.text))), None)
        if matched:
            chosen.append(ImagePlacement(asset_id=matched.id, caption=matched.title,
                position=f'第{index + 1}段后', block_index=index, role='detail',
                reason='素材说明与本段主题相交；不据图片推导性能或兼容性'))
        if len(chosen) >= limit:
            break
    return chosen


def editorial_review(task, result):
    """Actionable heuristics, not a fabricated popularity or conversion score."""
    findings = []
    def warn(message):
        findings.append(AuditItem(severity='warning', category='editorial', message=message))
    if not result.title.strip():
        warn('缺少明确标题或开场句')
    if task.platform == 'xiaohongshu':
        if len(result.title) > 20:
            warn('小红书标题偏长，建议压缩为一个具体问题或阅读收益')
        invitation=re.search(r'(?:评论|留言|告诉我|私信).{0,24}(?:专业|软件|需求|问题|顾虑|哪|什么)',result.cta)
        if not invitation and '收藏' not in result.cta and not any(('？' in b.text or '?' in b.text) for b in result.blocks) and not re.search(r'什么|哪|？|\?', result.cta):
            warn('笔记缺少与选购问题有关的具体互动问题')
        if any(len(b.text) > 240 for b in result.blocks):
            warn('笔记段落偏长，建议拆成一屏能读完的小段')
    elif task.platform == 'moments':
        total=len(result.title)+len(result.intro)+len(result.cta)+sum(len(b.text) for b in result.blocks)
        if len(result.blocks) > 2 or total > 180:
            warn('朋友圈信息过密，建议只保留一个具体困扰和一个回应')
        if len(result.tags) > 1:
            warn('朋友圈不宜直接套用小红书的话题标签串')
    elif task.platform == 'wechat':
        if len(result.blocks) < 3:
            warn('公众号缺少展开论证，建议补上场景、方案解释和选购边界')
    elif task.platform == 'poster':
        if len(result.title) > 18 or len(result.intro) > 45:
            warn('海报标题或副标题偏长，建议控制为一眼可读的核心信息')
        if any(len(b.text)>45 for b in result.blocks):
            warn('海报卖点过长，建议每条只讲一个可核验的利益点，完整条件保留在脚注')
    if task.platform!='wechat':
        actions=sum(bool(re.search(pattern,result.cta)) for pattern in ('收藏','评论|留言','私信|咨询','到店|预约'))
        if actions>1:
            warn('行动引导包含多个目标，建议只保留本次最重要的一步')
    formula = ['给你的一份场景选购草稿', '首先，其次，最后', '赋能你的', '开启全新体验']
    if any(term in ' '.join([result.intro, *[b.text for b in result.blocks]]) for term in formula):
        warn('存在模板式营销表达，需要改成客户的具体动作和问题')
    if not findings:
        findings.append(AuditItem(severity='info', category='editorial',
            message='通过基础平台结构检查；阅读吸引力与咨询效果仍需人工评阅和实际发布验证'))
    return findings


def preview_content_plan(task):
    return {
        'interaction_goal': task.goal or '帮助读者判断适合与否，再沟通一个具体需求',
        'reader_value': '看懂自己的使用条件，带着问题核对产品，而不是只看参数',
        'hook': '从客户已描述的阻碍切入；未描述的阻碍只作为待确认问题',
        'cover_copy': '先看任务，再选电脑',
        'image_brief': [
            {'role': 'cover', 'intent': '一个明确问题与同型号产品素材，避免虚构客户实拍'},
            {'role': 'detail', 'intent': '只选与具体段落相符的素材；缺少时提出补图需求，不凑数量'},
        ],
        'basis': '本地结构演练，不是模型创作结果或流量预测',
    }


def finalize_editorial_layout(task, result, bundle):
    """Bound the channel layout before the same fact and semantic audits run.

    Never invent shorter marketing claims. Oversized poster paragraphs become
    compact, verbatim evidence labels; all conditions stay publicly attached.
    Unrelated pictures become explicit production gaps, never substitute photos.
    """
    changes=[]
    if task.platform=='moments':
        if result.intro and not re.search(r'亲测|我用了|保证|支持|搭载|配备|\d+\s*(?:GB|TB|元|英寸)',result.intro,re.I):
            result.intro=''
            changes.append('朋友圈去掉重复导语，保留开场、正文和一个回应入口')
        if result.blocks and re.search(r'你.{0,25}[？?]|私信|留言|预约',result.blocks[-1].text) and result.cta:
            result.cta=''
            changes.append('正文末段已有互动入口，去掉重复行动引导')
    if task.platform=='poster' and any(len(b.text)>45 for b in result.blocks):
        by_id={f.id:f for f in bundle.facts}
        blocks=[]
        for block in result.blocks:
            for fid in block.fact_ids:
                fact=by_id.get(fid)
                if not fact or fid in {x for b in blocks for x in b.fact_ids}:
                    continue
                if '特色应用' in fact.label:
                    names=[x.strip() for x in re.split('[、，,]',fact.value) if x.strip() and x.strip() in block.text]
                    value='可了解：'+'、'.join(names[:2]) if names else ''
                else:
                    label=fact.label.split('/')[-1].strip()
                    value=label+'：'+fact.value
                if value and len(value)<=45:
                    blocks.append(ContentBlock(text=value,fact_ids=[fid],disclosures=list(fact.qualifiers)))
        if blocks:
            result.blocks=blocks[:3]
            changes.append('海报长段改为短事实标签，原始条件保留在公开脚注，随后重新核验')
    kept=[]
    gaps=[]
    for image in result.images:
        candidate=result.model_copy(update={'images':[image]})
        errors=[a.message for a in image_editorial_audit(candidate,bundle) if a.severity=='error']
        if errors:
            gaps.append({'status':'待补素材','asset_id':image.asset_id,
                'block_index':image.block_index,'intent':'需要与该段落匹配且描述可靠的画面',
                'reason':'；'.join(errors)})
        else:
            kept.append(image)
    if gaps:
        result.images=kept
        previous=result.content_plan.get('image_brief',[])
        result.content_plan['image_brief']=(previous if isinstance(previous,list) else [])+gaps
        changes.append(f'移除 {len(gaps)} 张主题、位置或说明不合格的配图；缺口进入补图计划，不凑数')
    return changes
