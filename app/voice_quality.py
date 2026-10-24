"""Reading protocol for the existing independent review, not an AI-style score."""
from copy import deepcopy


ORDINARY_READING_PROTOCOL = [
    '先只读headline_first中的原样标题和导语，按普通中文语义理解是谁、在说谁、具体动作或评价朝向；不要先借产品资料替标题解释。',
    '再看公开正文，区分发布者真实记录、一般情境、读者可能有的处境和推广产品；检查悬念是否就近自然解开。',
    '最后对照本次身份、平台与任务，判断重要误归、承诺落空或关系矛盾；没有冲突就保留表达，不要求改成正向广告。',
]

VOICE_REVIEW_INSTRUCTION = '''
voice_review_context提供本次普通阅读顺序，不是要求一律找错。先锁定真实公开文字，再给偏好或改稿建议。
reader_value.units[*].first_screen须写普通读者首读标题/首屏会理解的主体、动作和评价方向，再说明正文是否自然承接；不要写作者希望读者怎么理解。
负担、失败、麻烦、沉等负面表达可用于真实痛点、合理批评和反转；不能仅因正文有参数，就把它重新解释为有价值、有分量、兴趣点或成功感。也不能挑最遥远的负面联想强行判错。
不把“有现场细节/短句/表情/不工整”当成活人证明；不要求每帖加入见闻、顾客对话、结论、完整三步法或到店邀请。
若正文新增实际操作建议，独立检查物件结构、操作后果与执行条件，不能用产品参数真实来替操作可行性背书。不为观察帖要求补步骤；无必要或无依据的方法可以删除，保留观察。检查物理量和所指对象，不把屏幕对角线当机身尺寸、重量当占地大小的证明。重要且可定位的操作或事实错配沿用world/product维度反馈，不扩写成公开免责声明。
检查表达是否属于这个实际发布身份：门店可以有个人态度、邀请与一般情境，但资料与设计稿不授权编造已发生亲历。明确第三人称小剧场和一般第一人称意见可以成立。
像同一种模板、情绪太满、措辞老套等偏好只放现有creative_effectiveness.improvements可选建议；只有可定位的任务落空、重要主体/身份矛盾或真实信息缺陷才按现有规则修订。
不要以生成者解释、上一轮通过或来源标签决定好坏，不输出活人感/网感分数，不预测推流和转化。
'''


def build_voice_review_context(task, draft, *, context=None, editorial_voice=None):
    """Project exact public first-glance copy; no customer identity inference."""
    from .marketing_surfaces import marketing_public_surfaces
    task = task.model_dump(mode='json') if hasattr(task, 'model_dump') else task
    if not isinstance(task, dict):
        raise ValueError('Voice review requires a task')
    context = context if isinstance(context, dict) else {}
    publisher = context.get('publisher') if isinstance(context.get('publisher'), dict) else {}
    # This is an editorial relationship, never an authority or access grant.
    identities = {'store', 'brand', 'regional_agent', 'creator', 'salesperson'}
    identity = publisher.get('kind')
    execution_identity = editorial_voice.get('publisher_identity') if isinstance(editorial_voice, dict) else None
    # prepare_marketing_context's historical store fallback is not an explicit
    # publishing-identity decision. Do not override a specific trusted context,
    # including an explicitly supplied store without this exact generic rule.
    generic_store = (identity == 'store' and publisher.get('relationship') == 'mixed_or_unknown'
                     and publisher.get('identity_rule') == '从真实经营者/店员身份发声，不擅自拥有顾客的旧机、职业、收入与购买经历。'
                     and not publisher.get('identity_source'))
    identity_source = 'marketing_context'
    if execution_identity in identities and (identity not in identities or generic_store):
        identity = execution_identity
        identity_source = 'server_creative_execution.editorial_voice'
    if identity not in identities:
        identity = 'store'
        identity_source = 'legacy_store_fallback'
    surfaces = marketing_public_surfaces(draft)
    headline = {key: surfaces[key] for key in ('title', 'intro') if surfaces.get(key, '').strip()}
    first = dict(headline)
    opening = next((key for key in surfaces if key.startswith('body:') and '.' not in key
                    and surfaces[key].strip()), None)
    if opening:
        first[opening] = surfaces[opening]
    elif surfaces.get('body', '').strip():
        first['body'] = surfaces['body']
    elif surfaces.get('text', '').strip():
        first['text'] = surfaces['text']
    return {'version': 'ordinary-reading.v1', 'publisher_identity': identity,
            'publisher_identity_source': identity_source,
            'relationship': str(publisher.get('relationship') or 'mixed_or_unknown')[:100],
            'platform': str(task.get('platform') or '')[:64],
            'headline_first': headline, 'first_glance': first,
            'reading_order': deepcopy(ORDINARY_READING_PROTOCOL),
            'speaker_experience_authority': 'Only actual supplied records authorize past-tense personal experience; general opinion and explicit scenarios remain valid.',
            'quality_basis': 'located public text and task fit, not AI-style detection or predicted engagement'}
