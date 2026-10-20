from __future__ import annotations
import re
import time
import hashlib
import asyncio
import inspect
import io
from pathlib import Path
from .models import (TaskInput, EvidenceBundle, RunResult, ContentBlock, ImagePlacement,
                     AuditItem, TraceItem, creative_review_blocks)
from .creative_connectors import get_connector
from .editorial import (bind_legacy_images, choose_preview_images, image_editorial_audit,
                        editorial_review, preview_content_plan, finalize_editorial_layout)
from .sales import build_sales_plan


PLATFORMS = {
    'xiaohongshu': {'name':'小红书','structure':'问题切入 → 场景短段 → 选购建议','tone':'口语、真实，不伪造亲测','min_images':1,'max_images':6,'cta':'围绕一个具体任务展开交流'},
    'wechat': {'name':'公众号','structure':'导语 → 场景分析 → 分节证据 → 选购建议','tone':'清晰、有信息密度','min_images':1,'max_images':4,'cta':'围绕本文任务咨询一个具体问题'},
    'moments': {'name':'门店朋友圈','structure':'一个场景 → 一个核心卖点 → 咨询引导','tone':'简短、直接、有人情味','min_images':1,'max_images':3,'cta':'私信沟通你的使用需求，预约到店体验'},
    'poster': {'name':'门店海报','structure':'主标题 → 副标题 → 不超过三个卖点 → 行动引导','tone':'精炼、醒目','min_images':1,'max_images':1,'cta':'扫码咨询或预约到店体验'},
}


def norm(value):
    return re.sub(r'[\s\-_·（）()]', '', value).casefold()


def qualifier_norm(value):
    """Typography changes are harmless; words, digits and negation stay significant."""
    return re.sub(r'[\W_]+','',value,flags=re.UNICODE).casefold()


def strip_selected_model_names(text, products):
    """Model identifiers contain digits, but do not authorize numeric claims.

    Match only selected names, allowing the omitted brand and harmless spacing.
    Digit/Latin boundaries ensure a selected Mate 80 cannot hide Mate 800 or 80GB.
    """
    names = {name for name in products if name}
    names.update(re.sub(r'^(?:HUAWEI|华为)\s*', '', name, flags=re.I) for name in tuple(names))
    for name in sorted(names, key=len, reverse=True):
        tokens = re.split(r'\s+', name.strip())
        pattern = r'(?<![A-Za-z0-9])' + r'\s*'.join(re.escape(token) for token in tokens) + r'(?![A-Za-z0-9])'
        text = re.sub(pattern, '', text, flags=re.I)
    return text


def connector_platform_rule(task, bundle=None, strategy=None):
    """Registered platforms declare their limits; built-ins retain compatibility."""
    connector=get_connector(task.platform,task.creative_connector)
    brief=connector.build_brief(task,bundle if bundle is not None else EvidenceBundle(product=task.product),strategy or {})
    rule=brief.get('platform_rule') or PLATFORMS.get(task.platform)
    required={'name','min_images','max_images','structure','tone','cta'}
    if not isinstance(rule,dict) or not required.issubset(rule):
        raise ValueError('Connector must declare complete platform_rule')
    if not isinstance(rule['min_images'],int) or not isinstance(rule['max_images'],int) or not 0<=rule['min_images']<=rule['max_images']<=12:
        raise ValueError('Invalid connector image limits')
    return rule


def scenario_plan(task, bundle):
    """Deterministic preview hypotheses; model can refine these within a fixed schema."""
    context=task.audience+' '+task.scene
    if re.search('设计|创作|剪辑|摄影|视频',context):
        jobs=['处理创作素材','在不同设备间流转文件','确认实际使用的软件与外设']
        concerns=['待确认：目标软件和插件是否支持','待确认：屏幕与配置是否满足实际工作流']
        keywords=['屏幕','特色应用','内存','接口','操作系统']
    elif re.search('商务|差旅|出差|会议|职场|办公',context):
        jobs=['通勤或差旅中携带','处理会议与文档任务','确认常用办公工具']
        concerns=['待确认：携带负担和接口需求','待确认：办公软件、外设与协作环境兼容性']
        keywords=['特色应用','重量','接口','电池','屏幕','操作系统']
    elif re.search('学生|学习|课堂|图书馆|新生|校园',context):
        jobs=['在课堂和图书馆查看资料','记录学习内容','确认课程所需软件']
        concerns=['待确认：课程软件和专业工具是否支持','待确认：经常携带时对重量和屏幕的偏好']
        keywords=['特色应用','重量','屏幕','内存','操作系统']
    else:
        jobs=['明确日常重要任务','验证所需软件和设备','比较适合自己的配置']
        concerns=['待确认：旧设备带来的具体阻碍','待确认：预算、配置和使用条件']
        keywords=['屏幕','内存','重量','接口']
    def score(f):
        return sum(len(keywords)-i for i,key in enumerate(keywords) if key in f.label)
    ordered=sorted(bundle.facts,key=score,reverse=True)
    return {'jobs':jobs,'concerns':concerns,'priority_fact_ids':[f.id for f in ordered],
            'fact_reasons':[{'fact_id':f.id,'reason':'与当前重要任务相关；须保留原文限制条件'} for f in ordered[:3]],
            'planning_method':'本地规则推测，待用户确认；非模型画像'}


def product_claim_sentences(text, facts=()):
    """Find affirmative product clauses, never arbitrary fact-value substrings.

    This is a conservative citation gate, not an entailment judge. Independent
    semantic review still checks the complete draft, including advice. Question
    predicates are removed locally: a question never exempts another assertion.
    """
    claims=[]
    for raw in re.split(r'[。！？!?；;\n，,]+|但是|不过|而且|并且|以及|但', str(text)):
        clause=raw.strip()
        if not clause:
            continue
        checked=re.sub(r'(?:是否|能否|可否|是不是|能不能|可不可以)(?:正常)?(?:支持|兼容|安装|运行|打开|使用|装|用|连接|投屏|搭载|配备|采用|具备|内置)?','',clause)
        checked=re.sub(r'支持不支持|兼容不兼容|能装不能装|支持(?:情况|条件|范围|机型|清单)|兼容(?:情况|条件|范围|性)','',checked)
        checked=re.sub(r'(?:支持|兼容|搭载|配备)(?:哪些|什么|哪种|哪款)','',checked)
        checked=re.sub(r'(?:哪些|哪种|什么)(?:软件|应用|文件|格式|设备|外设)(?:可以|可|能)(?:安装|运行|打开|连接|使用|装)','',checked)
        checked=re.sub(r'(?:支持|兼容|能安装|能打开|可安装|可打开|可以安装|可以打开).{0,35}[吗么]','',checked)
        # Numeric specifications remain claims even inside a leading question;
        # authors should ask "重量多少" rather than seed an unsupported value.
        parameter=re.search(r'\d+(?:\.\d+)?\s*(?:GB|TB|MHz|GHz|Hz|英寸|毫米|小时|%|kg|mm|nits?|g\b|W\b|Wh\b|元)',checked,re.I)
        affirmative=re.search(r'(?:搭载|配备|具备|内置|重约|仅重|续航达|售价|到手价|保证兼容|完全兼容|全面兼容)',checked)
        compatibility=re.search(r'(?:支持|不支持|兼容|不兼容|可(?:以)?运行|能运行|可(?:以)?安装|能安装|可(?:以)?打开|能打开|可(?:以)?连接|能连接)\s*(?:所有|全部|各类|任何|多种|该|这|常用|课程|专业|软件|应用|文件|格式|设备|外设|触控|手写|多屏|跨设备|华为|USB|Windows|Harmony|鸿蒙|安卓|投屏|WPA|星闪|蓝牙)',checked,re.I)
        reverse_capability=re.search(r'(?:软件|应用|文件|格式|设备|外设).{0,8}(?:都)?(?:能|可以|可)(?:安装|运行|打开|连接|使用|装)',checked)
        named_capability=re.search(r'(?<!感谢)(?<!谢谢)(?:支持|兼容)\s*(?:[A-Za-z][\w.+-]+|剪映|钉钉|飞书|微信|绘图|视频剪辑)',checked,re.I)
        # "采用清单整理资料" is a method; "采用OLED屏幕" is a claim.
        adoption=re.search(r'采用.{0,20}(?:屏幕|处理器|芯片|内存|存储|电池|接口|OLED|LCD|CPU|GPU)',checked,re.I)
        field_assertion=re.search(r'(?:操作系统|内存|存储|屏幕|重量|处理器|CPU|电池容量|触摸屏|触控|星闪)\s*(?:为|是|[:：])\s*\S+',checked,re.I)
        bare_spec=re.fullmatch(r'(?:HarmonyOS|Windows|OLED|麒麟|Intel|AMD|锐龙|酷睿).{0,60}',checked,re.I)
        # Do not let short extracted values ("支持", "是", "有", "1")
        # turn an unrelated word or enumerated recommendation into a claim.
        if parameter or affirmative or compatibility or reverse_capability or named_capability or adoption or field_assertion or bare_spec:
            claims.append(clause)
    return claims


def has_product_claim(text, facts):
    return bool(product_claim_sentences(text,facts))


class AgentWorkflow:
    def __init__(self, provider, llm=None):
        self.provider, self.llm = provider, llm

    async def _official_cover(self, task, result, bundle):
        """Assemble an omitted cover from reviewed bytes, before all audits.

        This is asset selection rather than a substitute audit: it is limited to
        same-product official overview material already present in this bundle.
        """
        if result.images or task.mode != 'local':
            return None
        from .local_knowledge import official_url
        from .visual_assets import review_asset_bytes
        from PIL import Image
        provider = self.provider
        for _ in range(4):
            if callable(getattr(provider, 'get_asset', None)):
                break
            provider = getattr(provider, 'base', None)
            if provider is None:
                return None
        getter = getattr(provider, 'get_asset', None)
        if not callable(getter):
            return None
        sources = {source.id: source for source in bundle.sources}
        limit = 12 * 1024 * 1024
        for asset in bundle.assets:
            source = sources.get(asset.source_id)
            if (norm(asset.product) != norm(task.product) or not asset.verified
                    or asset.provenance != 'huawei_official'
                    or asset.visual_role != 'product_overview'
                    or asset.semantic_basis != 'pixel_review_sha256'
                    or not re.fullmatch(r'[0-9a-f]{64}', asset.sha256)
                    or not source or source.provider != 'huawei_official'
                    or not official_url(source.url)):
                continue
            try:
                value = getter(asset.id)
                if inspect.isawaitable(value):
                    value = await value
                if isinstance(value, tuple):
                    raw = value[0]
                elif isinstance(value, (str, Path)):
                    path = Path(value)
                    if not path.is_file() or path.stat().st_size > limit:
                        continue
                    raw = await asyncio.to_thread(path.read_bytes)
                else:
                    raw = value
                if not isinstance(raw, bytes) or not raw or len(raw) > limit:
                    continue
                if hashlib.sha256(raw).hexdigest() != asset.sha256:
                    continue
                with Image.open(io.BytesIO(raw)) as image:
                    if image.format not in ('JPEG', 'PNG', 'WEBP'):
                        continue
                    image.verify()
                reviewed = review_asset_bytes(raw, asset.original_title or asset.title)
                if (not reviewed.get('usable') or reviewed.get('semantic_basis') != 'pixel_review_sha256'
                        or reviewed.get('visual_role') != 'product_overview'):
                    continue
            except Exception:
                continue
            result.images = [ImagePlacement(asset_id=asset.id, caption='官方产品资料图', position='封面',
                                             role='cover', block_index=None, supported_fact_ids=[],
                                             reason='采用本次同型号官方素材中已按图片哈希审阅的产品总览图作为封面')]
            return asset.id
        return None

    def _audit(self, task, result, bundle):
        issues = []
        def add(category,message):
            issues.append(AuditItem(severity='error',category=category,message=message))
        source_map = {s.id:s for s in bundle.sources}
        fact_map = {f.id:f for f in bundle.facts}
        asset_map = {a.id:a for a in bundle.assets}
        product_scope = {norm(task.product), *(norm(name) for name in task.ecosystem_products)}
        if task.mode in ('local','live') and norm(task.product) != norm(bundle.product):
            add('model','任务指定型号与知识包型号不一致，需要确认准确型号')
        if task.mode == 'live' and any(s.provider != 'ima' for s in bundle.sources):
            add('evidence','实时模式来源必须全部来自ima')
        if task.mode == 'local' and any(s.provider not in ('local_archive','huawei_official','store_knowledge','store_upload') for s in bundle.sources):
            add('evidence','正式本地模式须使用赛事附件或华为官方原文，不能用演练资料替代')
        for fact in bundle.facts:
            source = source_map.get(fact.source_id)
            if not source or not fact.quote.strip() or fact.quote not in source.excerpt:
                add('evidence',f'事实 {fact.id} 原文摘录无法在来源中精确定位')
            if task.mode=='local' and source and (not source.sha256 or source.sha256!=hashlib.sha256(source.excerpt.encode('utf-8')).hexdigest()):
                add('evidence',f'本地正式事实 {fact.id} 的原文缺少可复核内容哈希')
            if norm(fact.product) not in product_scope:
                add('model',f'事实 {fact.id} 型号与选定产品不一致')
            if norm(fact.product)==norm(bundle.product) and fact.system and bundle.system and norm(fact.system) != norm(bundle.system):
                add('system',f'事实 {fact.id} 的操作系统与产品知识包不一致')
            if fact.value not in fact.quote:
                add('evidence',f'事实 {fact.id} 的值不在对应原文摘录中')
        if not result.blocks:
            add('structure','正文为空')
        review_blocks=[*result.blocks,*creative_review_blocks(result.creative_plan)]
        card_labels=[f'创意卡片 {c.get("id",i+1)}' for i,c in enumerate(result.creative_plan.get('cards',[]))]
        variant_labels=[f'标题封面候选 {i+1}' for i,_ in enumerate(result.creative_plan.get('test_variants',[]))]
        block_labels=[f'正文第{i+1}段' for i in range(len(result.blocks))]+card_labels+variant_labels
        for block_index,block in enumerate(review_blocks):
            location=block_labels[block_index] if block_index<len(block_labels) else f'图文片段 {block_index+1}'
            valid = [fact_map[x] for x in block.fact_ids if x in fact_map]
            if len(valid) != len(block.fact_ids):
                add('citation',f'{location}引用不存在的事实ID')
            if not valid:
                for sentence in product_claim_sentences(block.text,bundle.facts):
                    add('citation',f'{location}存在未引用的产品陈述：『{sentence[:180]}』；请引用对应事实，或改为待核对的问题')
            # A broad source section may contain other fields; citing its weight
            # record does not authorize unrelated dimensions or interface values.
            allowed = ' '.join(f.value+' '+' '.join(f.qualifiers) for f in valid)
            # Named ecosystem features are not interchangeable across OS families.
            for feature in ('多屏协同','跨设备剪贴板','应用接续','键鼠共享','华为分享','超级终端'):
                if (valid and feature in block.text and feature not in allowed
                        and re.search(r'(?:含|提供|支持|搭载|具备|应用).{0,100}'+feature,block.text)):
                    add('citation',f'功能名「{feature}」未出现在所引事实中，不能用近似功能或“类功能”替代')
            for disclosure in block.disclosures:
                if qualifier_norm(disclosure) and qualifier_norm(disclosure) not in qualifier_norm(allowed):
                    add('qualifier','脚注包含所引事实及限定之外的内容，需保留原文或删除新增主张')
            numbered_text=re.sub(r'(?m)^\s*(?:\d+(?:\.(?!\d)|[、）)])|第\d+[章节步点])[ \t]*','',block.text)
            numbered_text=strip_selected_model_names(numbered_text, [task.product, *task.ecosystem_products])
            for number in re.findall(r'\d+(?:\.\d+)?',numbered_text) if valid or has_product_claim(numbered_text,bundle.facts) else []:
                if number not in re.findall(r'\d+(?:\.\d+)?',allowed):
                    add('number',f'正文出现所引证据未包含的数字 {number}')
            for f in valid:
                for qualifier in f.qualifiers:
                    if qualifier and qualifier_norm(qualifier) not in qualifier_norm(block.text+' '+' '.join(block.disclosures)):
                        add('qualifier',f'正文遗漏事实 {f.id} 的限定条件：{qualifier}')
                if '右侧' in f.label and '供电' in f.value and '供电' in block.text:
                    if not re.search(r'右侧[^。；;\n]{0,160}供电',block.text):
                        add('qualifier','对外供电能力必须明确归属右侧接口，不能泛化到左侧或所有接口')
        other = ' '.join([result.title,result.intro,result.cta,*result.tags])
        if re.search(r'\d+(?:\.\d+)?\s*(?:元|GB|TB|Hz|英寸|毫米|小时|%|g|kg|mm|nit|W)\b',other,re.I):
            add('citation','标题/导语/行动引导含未逐条引用的参数或价格')
        all_text = other + ' '.join(b.text+' '+' '.join(b.disclosures) for b in review_blocks) + ' '.join(i.caption for i in result.images)
        for term in ['全国第一','行业第一','全网最低','百分百','100%保证','绝对不卡','永久免费','闭眼买','遥遥领先','最强','顶级','保证兼容','亲测','我用了']:
            if term in all_text:
                add('compliance',f'需删除无依据或高风险表达：{term}')
        if re.search(r'国补|补贴|优惠|到手价|赠送|销量|获奖',all_text):
            supporting = ' '.join(f.quote for f in bundle.facts)
            for term in re.findall(r'国补|补贴|优惠|到手价|赠送|销量|获奖',all_text):
                if term not in supporting:
                    add('price',f'缺少知识库依据：{term}')
        rule = connector_platform_rule(task,bundle,result.strategy)
        if not rule['min_images'] <= len(result.images) <= rule['max_images']:
            severity='warning' if task.mode=='demo' and len(result.images)<rule['min_images'] else 'error'
            issues.append(AuditItem(severity=severity,category='image_count',message=f"{rule['name']}须 {rule['min_images']}-{rule['max_images']} 张图，目前 {len(result.images)} 张；不足时不能作为发布成品"))
        if len(set(i.asset_id for i in result.images)) != len(result.images):
            add('image','不能重复图片填充配图数量')
        for placement in result.images:
            asset = asset_map.get(placement.asset_id)
            if not asset:
                add('image','图片ID不在本次检索素材集合中')
                continue
            if norm(asset.product) not in product_scope:
                add('image','图片型号与选定型号不一致')
            if asset.source_id not in source_map:
                add('image','图片缺少本次检索来源')
            if task.mode == 'live' and (not asset.verified or asset.provenance != 'ima_direct'):
                add('image','图片尚未确认来自本次ima直接返回素材')
            if task.mode == 'local' and (not asset.verified or asset.provenance not in ('local_archive','huawei_official','store_upload') or not re.fullmatch(r'[0-9a-f]{64}',asset.sha256)):
                add('image','本地正式图片缺少附件/官方来源或图片完整性记录')
        for card in result.creative_plan.get('cards', []):
            for asset_id in card.get('asset_ids', []):
                asset=asset_map.get(asset_id)
                if not asset:
                    add('image','创意卡片引用的图片不在本次检索素材集合中')
                    continue
                if norm(asset.product) not in product_scope or asset.source_id not in source_map:
                    add('image','创意卡片图片型号或来源不匹配')
                if task.mode=='live' and (not asset.verified or asset.provenance!='ima_direct'):
                    add('image','创意卡片图片尚未完成ima直接来源验证')
                if task.mode=='local' and (not asset.verified or asset.provenance not in ('local_archive','huawei_official','store_upload') or not re.fullmatch(r'[0-9a-f]{64}',asset.sha256)):
                    add('image','创意卡片图片缺少本地正式来源或图片完整性记录')
                if card.get('supported_fact_ids') and asset.visual_role in ('product_overview','unknown'):
                    add('image','产品外观图不能在创意卡片中作为功能或场景实证，请改用信息卡或匹配素材')
                if asset.provenance == 'store_upload':
                    if card.get('supported_fact_ids'):
                        add('image','门店上传照片只作场景素材，不能作为创意卡片中产品参数或功能的证明')
                    if card.get('truth_status') == 'official_material':
                        add('image','门店上传照片应标为真实照片，不能改标成官方产品素材')
        if task.platform == 'poster' and len(result.blocks) > 3:
            add('platform','海报卖点不能超过三个')
        issues.extend(image_editorial_audit(result,bundle))
        connector=get_connector(task.platform,task.creative_connector)
        issues.extend(connector.review(result.creative_plan,result,bundle))
        if not result.creative_plan:
            issues.append(AuditItem(severity='warning',category='creative',message='本次未生成创意分镜；仅保留旧版图文，不能宣称已完成卡片策划'))
        if bundle.conflicts:
            for conflict in bundle.conflicts:
                add('conflict',conflict)
        return issues

    async def run(self, task: TaskInput) -> RunResult:
        result = RunResult(mode=task.mode)
        started = time.monotonic()
        def trace(stage,detail):
            result.trace.append(TraceItem(stage=stage,detail=detail,duration_ms=int((time.monotonic()-started)*1000)))
        def model_metrics():
            existing=sum(t.stage=='model_usage' for t in result.trace)
            for metric in getattr(self.llm,'metrics',[])[existing:]:
                result.trace.append(TraceItem(stage='model_usage',duration_ms=metric['duration_ms'],
                    detail=f"模型调用 {metric['call']}/4；{metric.get('outcome','success')}；输入 {metric['prompt_tokens']} tokens，输出 {metric['completion_tokens']} tokens，总计 {metric['total_tokens']} tokens"))
        def semantic_verdict(items):
            supported=any(a.severity=='info' and a.category=='semantic' and
                re.search('直接支持|核验通过|事实.*支持|supported',a.message,re.I) for a in items)
            if not supported:
                items=list(items)+[AuditItem(severity='error',category='semantic',message='独立语义审计未明确确认产品主张均获支持，不能放行')]
            return items
        missing = [label for field,label in [('product','准确型号（含代际/系统）'),('audience','目标人群'),('scene','使用场景')] if not getattr(task,field).strip()]
        if missing:
            result.status='needs_input'
            result.questions=[f'请补充{label}。' for label in missing]
            trace('clarify','需要补充任务字段')
            return result
        try:
            connector=get_connector(task.platform,task.creative_connector)
            rule=connector_platform_rule(task)
        except (ValueError,KeyError):
            result.status='blocked'
            result.audit.append(AuditItem(severity='error',category='creative',message='创意连接器不存在或与平台不匹配'))
            return result
        result.strategy={**rule,'audience':task.audience,'scene':task.scene,'stage':task.stage,
                         'goal':task.goal or '让用户理解产品与自身需求的关系，并预约验证',
                         'angle':f'从{task.audience}在{task.scene}中的实际任务与阻碍切入',
                         'evidence_policy':'仅使用本次检索原文；不推断软件兼容、补贴资格或个人体验'}
        trace('strategy','已确定人群、场景、购买阶段与平台策略')
        try:
            bundle = EvidenceBundle.model_validate(await self.provider.retrieve(task.product, f'{task.audience} {task.scene} {task.requirements}'))
        except Exception as exc:
            result.status='error'
            result.audit.append(AuditItem(severity='error',category='retrieval',message=f'知识检索失败（{type(exc).__name__}），请检查连接与权限'))
            trace('retrieval','检索未完成')
            return result
        result.facts=bundle.facts
        # Public result carries opaque asset routes and source titles, never private paths or signed URLs.
        result.assets=[a.model_copy(update={'url':'/api/assets/'+a.id,'local_path':''}) for a in bundle.assets]
        from .local_knowledge import official_url
        result.sources=[s.model_copy(update={'url':s.url if s.provider=='huawei_official' and official_url(s.url) else ''}) for s in bundle.sources]
        result.audit.extend(AuditItem(severity='warning',category='retrieval',message=w) for w in bundle.warnings)
        result.strategy.update(scenario_plan(task,bundle))
        trace('retrieval',f'收到 {len(bundle.facts)} 条事实、{len(bundle.assets)} 张素材和 {len(bundle.sources)} 个来源')
        if bundle.conflicts or not bundle.facts or (task.mode=='live' and not bundle.verified_live) or (task.mode=='local' and not bundle.verified_local):
            result.status='blocked'
            result.audit.extend(AuditItem(severity='error',category='conflict',message=c) for c in bundle.conflicts)
            result.audit.append(AuditItem(severity='error',category='evidence',message='存在资料冲突、缺少产品事实或尚未完成所选来源验证；停止生成'))
            return result
        enabled=bool(self.llm and getattr(self.llm,'enabled',True))
        if task.mode in ('local','live') and not enabled:
            result.status='blocked'
            result.audit.append(AuditItem(severity='error',category='model',message='未配置可调用模型，正式模式不能用模板代替真实生成和审计'))
            return result
        creative_plan_errors=[]
        cover_assembly_allowed = False
        def apply(data):
            nonlocal creative_plan_errors, cover_assembly_allowed
            creative_plan_errors=[]
            # A repair replaces the plan. Never retain stale cards from a prior
            # draft when the repaired model response omits them.
            try:
                result.creative_plan=connector.normalize_plan(data.get('creative_plan'),task,bundle)
                if getattr(self.llm,'supports_creative_plan',False) and not result.creative_plan.get('cards'):
                    creative_plan_errors.append(AuditItem(severity='error',category='creative_missing',
                        message='平台创意生成器未返回有效分镜卡片，必须生成creative_plan.cards，不能将普通文案视为已接入成品'))
            except (ValueError,TypeError):
                result.creative_plan={}
                creative_plan_errors.append(AuditItem(severity='error',category='creative_schema',
                    message='创意分镜结构无效，必须在本轮修复中重新生成；未将无效卡片作为成品输出'))
            proposal=data.get('strategy',{})
            if isinstance(proposal,dict):
                valid_ids={f.id for f in bundle.facts}
                for key in ['angle','jobs','concerns']:
                    if key in proposal and isinstance(proposal[key],str if key=='angle' else list):
                        result.strategy[key]=proposal[key][:1000] if key=='angle' else [str(x)[:400] for x in proposal[key][:6]]
                if isinstance(proposal.get('priority_fact_ids'),list):
                    result.strategy['priority_fact_ids']=[x for x in proposal['priority_fact_ids'] if isinstance(x,str) and x in valid_ids][:10]
                if isinstance(proposal.get('fact_reasons'),list):
                    result.strategy['fact_reasons']=[{'fact_id':x['fact_id'],'reason':str(x.get('reason',''))[:400]}
                        for x in proposal['fact_reasons'] if isinstance(x,dict) and x.get('fact_id') in valid_ids][:10]
                if proposal.get('jobs') and proposal.get('concerns') and proposal.get('priority_fact_ids'):
                    result.strategy['planning_method']='模型任务分析与证据排序；平台约束由程序锁定'
            for key in ['title','intro','cta']:
                setattr(result,key,str(data.get(key,'')))
            result.tags=[str(x) for x in data.get('tags',[])]
            result.blocks=[ContentBlock.model_validate(x) for x in data.get('blocks',[])]
            result.images=bind_legacy_images([ImagePlacement.model_validate(x) for x in data.get('images',[])],len(result.blocks))
            # Editorial cleanup may remove a valid product overview that the
            # model incorrectly positioned as a detail image. Reassemble only
            # when all original references themselves were legitimate; never
            # hide an unknown ID, wrong model, missing source or unverified item.
            by_asset_id = {asset.id: asset for asset in bundle.assets}
            source_ids = {source.id for source in bundle.sources}
            fact_ids = {fact.id for fact in bundle.facts}
            selected_products = {norm(task.product), *(norm(name) for name in task.ecosystem_products)}
            cover_assembly_allowed = all(
                (asset := by_asset_id.get(image.asset_id)) is not None
                and norm(asset.product) in selected_products
                and asset.source_id in source_ids and asset.verified
                and asset.provenance in ('local_archive', 'huawei_official', 'store_upload')
                and re.fullmatch(r'[0-9a-f]{64}', asset.sha256)
                and set(image.supported_fact_ids).issubset(fact_ids)
                for image in result.images)
            if len({image.asset_id for image in result.images}) != len(result.images):
                cover_assembly_allowed = False
            if isinstance(data.get('content_plan'),dict):
                result.content_plan={k:v for k,v in data['content_plan'].items() if k in {
                    'title_candidates','hook','reader_value','cover_copy','image_brief','interaction_goal','publication_note'}}
            for change in finalize_editorial_layout(task,result,bundle):
                trace('editorial_edit',change)
        try:
            if enabled:
                apply(await self.llm.generate(task,bundle,result.strategy))
                if cover_assembly_allowed and await self._official_cover(task, result, bundle):
                    trace('cover_assembly','已从本次同型号官方素材中选入哈希匹配且已审阅的产品封面，随后执行完整审查')
                result.model_used=getattr(self.llm,'model','configured-model')
            else:
                ranking={fid:i for i,fid in enumerate(result.strategy['priority_fact_ids'])}
                facts=sorted(bundle.facts,key=lambda f:ranking.get(f.id,999))[:1 if task.platform=='moments' else 3]
                lead={
                    'xiaohongshu':f'{task.scene}怎么选电脑？先把每天要做的任务列清楚',
                    'wechat':f'面向{task.audience}的{task.scene}选购思路',
                    'moments':f'{task.scene}选电脑，先聊最在意的一个问题',
                    'poster':f'让{task.scene}的选择更清楚',
                }.get(task.platform,f'{task.scene}的任务核对清单')
                def preview_fact(f):
                    if '特色应用' in f.label:
                        choices=[name for name in ['华为笔记','小艺慧记','应用接续','跨设备剪贴板'] if name in f.value]
                        value='、'.join(choices[:3]) if choices else f.value
                        text=f'围绕自己的实际任务，可先了解官方资料列出的应用：{value}。'
                    else:
                        text=f'{f.label}：{f.value}。'
                    return text+''.join(f.qualifiers)+'。选购时可结合自己的使用习惯到店确认。'
                apply({'title':lead,'intro':f'给{task.audience}的一份场景选购草稿。',
                    'blocks':[{'text':preview_fact(f),'fact_ids':[f.id]} for f in facts],
                    'cta':rule['cta'],'tags':[task.scene] if task.platform=='xiaohongshu' else [],
                    'images':[]})
                result.images=choose_preview_images(task,bundle,result.blocks) if task.platform in PLATFORMS else []
                result.content_plan=preview_content_plan(task)
                result.model_used='本地模板演示（无模型调用）'
            trace('generation','已生成图文草稿及事实引用')
            deterministic = self._audit(task,result,bundle)+creative_plan_errors
            result.audit.extend(deterministic)
            result.editorial_review=editorial_review(task,result)
            if enabled:
                semantic=semantic_verdict(await self.llm.verify(result,bundle))
                result.audit.extend(semantic)
                trace('semantic_verification','完成独立语义事实核验')
                if (any(a.severity=='error' for a in result.audit) or any(a.severity=='warning' for a in result.editorial_review)) and hasattr(self.llm,'repair'):
                    apply(await self.llm.repair(task,bundle,result.strategy,result))
                    if cover_assembly_allowed and await self._official_cover(task, result, bundle):
                        trace('cover_assembly','修订稿选入本次已核对官方产品封面，随后重新执行完整审查')
                    result.audit=[a for a in result.audit if a.category=='retrieval']
                    result.audit.extend(self._audit(task,result,bundle))
                    result.audit.extend(creative_plan_errors)
                    result.audit.extend(semantic_verdict(await self.llm.verify(result,bundle)))
                    trace('repair','已执行唯一一次修复并重新核验')
            else:
                result.audit.append(AuditItem(severity='warning',category='semantic',message='未运行模型语义核验，不能用于发布'))
        except Exception as exc:
            model_metrics()
            result.status='blocked'
            detail=getattr(exc,'safe_message',type(exc).__name__)
            explanation={
                'invalid_model_json':'模型返回的成稿格式不完整，自动重试后仍未恢复。',
                'empty_model_content':'模型返回了空内容，自动重试后仍未恢复。',
                'invalid_model_response':'模型服务返回了无法识别的响应。',
                'output_token_budget_exceeded':'模型输出超过本次长度上限，成稿未能完整返回。',
                'call_budget_exceeded':'本次生成与修改次数已用完，仍有问题需要处理。',
            }.get(detail,f'模型生成或内容审核未完成（{detail}）。')
            result.audit.append(AuditItem(severity='error',category='model',message=explanation+'需求已保留，可重新生成。'))
            return result
        errors=any(a.severity=='error' for a in result.audit)
        result.editorial_review=editorial_review(task,result)
        result.sales_plan=build_sales_plan(task,bundle,result.strategy)
        model_metrics()
        result.status='blocked' if errors else ('ready' if task.mode in ('local','live') else 'draft')
        if task.mode=='demo':
            result.audit.append(AuditItem(severity='warning',category='demo',message='独立演练模式：结果仅为草稿，不代表正式生成与审查已完成'))
        trace('complete','已完成硬约束、来源与合规核验；'+('允许导出已审核稿' if result.status=='ready' else '保留草稿或阻断状态'))
        return result
