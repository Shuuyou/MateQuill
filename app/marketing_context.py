"""Bounded, automatic editorial evidence; never a measured customer profile.

Public search snippets are leads with timestamps, not certified facts. They
allow the reviewer to identify concrete uncertainty and remove unsupported
claims without asking a salesperson to fill a research form.
"""
from __future__ import annotations

import asyncio
from copy import deepcopy
from datetime import datetime
import json
import re
from zoneinfo import ZoneInfo

from .place_discovery import discover_place

POLICY_VERSION = '2026-10-23.1'
POLICIES = [
    {'id': 'advertising-identifiability-2024', 'kind': 'official_rule',
     'url': 'https://www.samr.gov.cn/zw/zfxxgk/fdzdgknr/ggjgs/art/2024/art_89824524f2804c5594e95408fbdf8602.html',
     'scope': '区分公开经营者自销身份、体验测评附购买方式等具体情形；不要假扮消费者。'},
    {'id': 'ai-label-2025', 'kind': 'official_rule',
     'url': 'https://www.cac.gov.cn/2025-03/14/c_1743654684782215.htm',
     'scope': '商业身份和生成合成内容标识分别处理；发布包装执行对应义务，不在正文泛化免责。'},
    {'id': 'advertising-qualifications-2026', 'kind': 'official_guidance',
     'url': 'https://www.samr.gov.cn/ggjgs/tzgg/art/2026/art_23fa8f1eaa5546af86528157fbc85db5.html',
     'scope': '主体不误导、必要性能条件清晰可读，不能用小字推翻大字；专项整治期限不冒充永久新增法律。'},
]


def public_query(value: str, limit=160) -> str:
    value = re.sub(r'https?://\S+|[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}|(?<!\d)1[3-9]\d{9}(?!\d)', '', str(value))
    return re.sub(r'[\r\n\t"“”「」]+', ' ', value).strip()[:limit]


async def _research(query, search):
    try:
        response = await asyncio.wait_for(search(query, use_cache=False), timeout=14)
        return {'query': query, 'state': response.get('state', 'unavailable'),
                'observed_at': response.get('observed_at'),
                'sources': [{key: row.get(key) for key in
                    ('source_id', 'title', 'url', 'excerpt', 'published_at', 'observed_at', 'evidence_kind')}
                    for row in response.get('items', [])[:5]]}
    except asyncio.CancelledError:
        raise
    except Exception:
        return {'query': query, 'state': 'unavailable', 'sources': []}


async def prepare_marketing_context(task, local_context=None, *, search=None):
    now = datetime.now(ZoneInfo('Asia/Shanghai'))
    product = public_query(task.product, 80)
    queries = [f'"{product}" {now:%Y年%m月} 官方 更新 公告',
               f'"{product}" {now:%Y年%m月} 争议 澄清 消费者'] if product else []
    location = public_query((local_context or {}).get('location_summary', ''), 120)
    city = re.search(r'(?:位于|地处|，|^)([\u4e00-\u9fff]{2,6}市)', location)
    if city:
        queries.append(f'"{city.group(1)}" {now:%Y年%m月} 本地新闻 通报')
    research = await asyncio.gather(*(_research(q, search or discover_place) for q in queries))
    return {'schema_version': 1, 'policy_version': POLICY_VERSION,
        'as_of': now.isoformat(), 'publisher': {'kind': 'store', 'relationship': 'mixed_or_unknown',
            'identity_rule': '从真实经营者/店员身份发声，不擅自拥有顾客的旧机、职业、收入与购买经历。'},
        'platform': task.platform, 'local_editorial_context': deepcopy(local_context),
        'origin_policy': {
            'public_place_evidence_licenses': ['公开位置与环境线索', '有依据的受众选题假设', '未来可尝试的生活动作'],
            'public_place_evidence_does_not_license': ['我亲自拍过或试过', '我每天看见或顾客经常告诉我', '本店窗口/楼层视线/体验设备/库存和服务已被确认'],
            'experience_rule': '真实已发生经历须有用户提供的可追溯记录；没有记录时用当下观察建议、未来邀请或明确假设，保留钩子，不编写回忆。',
            'visual_rule': '没有本店照片与视线依据时，不画貌似从本店窗口实拍的当地景色；生成场景是创意表达，不能充当门店现场或本机样张证据。',
        },
        'policies': deepcopy(POLICIES), 'research': research,
        'evidence_limits': [
            '公开检索摘要是线索，不是已读全文，也不证明没有争议；检索时间不等于事实生效时间。',
            '当地公开场景支持选题假设，不证明好友比例、个人处境、收入或购买阶段。',
            '没有借势或时效断言时，timeliness可not_applicable；不要求为了审稿人为加入热点。',
            '后台审核覆盖已作出的具体主张，不要求文案补全所有参数、法规或免责声明。'],
        'tone_goal': '保留好奇、幽默、双关、审美与具体生活价值；不以贬低顾客/旧机/职业制造购买补偿。'}


async def enrich_review_evidence(context, review, *, search=None):
    """Research only specific world/time findings; never send full private task."""
    quotes = []
    for name in ('world', 'timeliness'):
        dimension = review.get('dimensions', {}).get(name, {})
        if dimension.get('status') not in ('unverified', 'revise', 'reselect'):
            continue
        for finding in dimension.get('findings', []):
            quote = public_query(finding.get('quote', ''), 100)
            if len(quote) >= 4 and quote not in quotes:
                quotes.append(quote)
    result = deepcopy(context)
    if quotes:
        research = await asyncio.gather(*(_research(f'"{q}" 来源 核实', search or discover_place) for q in quotes[:2]))
        result['targeted_research'] = research
    return result


def replace_rejected_scene(task, review):
    """Update the canonical editorial hypothesis, preserving location evidence.

    This is a new creative instruction, never a new geographical/customer fact.
    Mutate the authorized task so downstream design receives the same revision.
    """
    from .gtm_agent.place_context import PLACE_CONTEXT_MARKER
    feedback = str(review.get('revision_brief') or '').strip()[:700]
    if not feedback:
        return
    # Feedback is a revision instruction, not a newly selected scene. Keep it
    # in history/strategy; feeding it into scene would turn optional suggestions
    # into mandatory scene-review requirements on the next pass.
    scene = '重新选择当地生活中的一个具体动作与观察（编辑提案，不是已发生经历）。'
    task.scene = scene
    if PLACE_CONTEXT_MARKER not in task.requirements:
        return
    prefix, serialized = task.requirements.rsplit(PLACE_CONTEXT_MARKER, 1)
    try:
        envelope, end = json.JSONDecoder().raw_decode(serialized.lstrip())
        suffix = serialized.lstrip()[end:]
        if not isinstance(envelope, dict) or not isinstance(envelope.get('creative_brief'), dict):
            return
        selected = envelope['creative_brief']
        place_anchor = str(selected.get('location_summary') or '').strip()[:250]
        if place_anchor:
            scene = ('保留公开周边环境线索（不等于本店设施或已发生经历）：' + place_anchor
                     + '；在此环境内重选未来可尝试的具体生活动作。')[:800]
            task.scene = scene
        envelope['reselection_history'] = [*envelope.get('reselection_history', []),
            {'scene': selected.get('scene'), 'reason': feedback}][-2:]
        selected['scene'] = scene[:800]
        selected['visual_direction'] = '围绕新选定生活动作制作，与实际新稿一致；修改反馈仅作编辑指导，不是必须画出的新场景。'
        task.requirements = prefix + PLACE_CONTEXT_MARKER + json.dumps(envelope, ensure_ascii=False) + suffix
    except (ValueError, TypeError):
        return
