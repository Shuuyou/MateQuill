"""Task-conditioned reader payoff review, inside the existing independent call.

This module does not call a model, publish content or certify image pixels. It
binds judgement to the exact public text and computes control from located gaps.
"""
from __future__ import annotations

from copy import deepcopy
import hashlib
import json
import re
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

Intent = Literal['tutorial', 'buying_tradeoff', 'local_experience', 'aesthetic_emotion',
                 'everyday_observation', 'open_expression']

RUBRICS = {
    'tutorial': '兑现本次操作问题，给读者能执行的方法或排查路径；不得只列规格后用到店CTA替代标题承诺。图卡可以承担步骤，封面不必独自讲完全部步骤。',
    'buying_tradeoff': '帮助读者理解本次任务中的实际取舍和选择依据；不按参数数量评优，不要求综合排名，不新增未经支持的适配结论。',
    'local_experience': '把本地生活切角接成自愿、具体且可理解的体验动作，读者知道能试什么或判断什么；不强制长教程、设施保证、优惠或免责声明。',
    'aesthetic_emotion': '兑现已选视觉、情绪或语言关系，检查具体画面感、态度与产品联系；允许少字、留白、双关和开放收尾，不强迫步骤、参数清单或购买CTA。文字审核不认证图片像素。',
    'everyday_observation': '交出一项具体可感知的观察或新的看法，让品牌自然参与；不要求教程、促销或结论式说教，不编造亲历。',
    'open_expression': '根据这次原始目标和实际标题识别阅读回报，可为方法、选择、体验、情绪或观察；不要把内部建议升级成新任务，也不以篇幅、清单或CTA代替价值。',
}

SUBJECT_ALIGNMENT_STANDARD = (
    '按本次任务辨认标题、首屏所说的体验/评价指向谁，以及正文实际营销的产品。'
    '负面痛点、反问、反转、真实缺点与审美双关均可用，不要求正向广告；'
    '但旧设备或用户处境的痛点不能在缺乏区分时被误归到所推产品，正文也不能靠切换指代偷偷改变标题主体。'
    '首屏可通过旧/新设备、时间、人物、场景或明确对比自然辨别主体，不强制出现“旧电脑”等固定用语。'
    '有意悬念若后续就近解开且不会使读者误认产品，应保留；主体已清楚且批评确实针对该产品时，不因负面而判错。'
    '若存在重要主体歧义，定位标题/首屏与正文对应片段，最小化澄清指代或对比关系，保留痛点、切角、语气及已有事实，不改成口号。'
)


class ReaderModel(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)


class ReaderCitation(ReaderModel):
    region: str = Field(min_length=1, max_length=300)
    quote: str = Field(default='', max_length=16000)


class ReaderGap(ReaderCitation):
    kind: Literal['unfulfilled_promise', 'missing_payoff', 'surface_mismatch',
                  'unusable_action', 'unrelated_brand', 'subject_ambiguity']
    reason: str = Field(min_length=1, max_length=1000)
    target: str = Field(min_length=1, max_length=1000)
    action: Literal['revise', 'reselect'] = 'revise'
    repair_surfaces: list[str] = Field(min_length=1, max_length=8)
    related_evidence: list[ReaderCitation] = Field(default_factory=list, max_length=4)

    @model_validator(mode='after')
    def subject_gap_needs_both_sides(self):
        if self.kind == 'subject_ambiguity' and not any(item.region != self.region for item in self.related_evidence):
            raise ValueError('Subject ambiguity needs distinct public context evidence')
        return self


class ReaderUnitReview(ReaderModel):
    unit_id: Literal['main_post', 'card_copy']
    status: Literal['met', 'gap', 'unverified']
    promise: str = Field(min_length=1, max_length=800)
    payoff: str = Field(min_length=1, max_length=1200)
    first_screen: str = Field(min_length=1, max_length=800)
    evidence: list[ReaderCitation] = Field(max_length=6)
    findings: list[ReaderGap] = Field(max_length=4)

    @model_validator(mode='after')
    def coherent(self):
        # A located defect cannot be hidden behind an author's aggregate pass.
        if self.findings and self.status == 'met':
            self.status = 'gap'
        if self.status != 'met' and not self.findings:
            raise ValueError('Unmet reader payoff needs a located repair target')
        if self.status == 'met' and not self.evidence:
            raise ValueError('Met reader payoff needs actual public evidence')
        return self


class ReaderValueReview(ReaderModel):
    contract_id: str = Field(pattern=r'^[a-f0-9]{64}$')
    intent: Intent
    units: list[ReaderUnitReview] = Field(min_length=1, max_length=2)
    preserve: list[ReaderCitation] = Field(max_length=8)
    # Derived, like MarketingReview.decision; never a numeric quality score.
    status: Literal['pass', 'revise', 'reselect', 'unverified'] = 'pass'
    scope_conflicts: list[str] = Field(default_factory=list, max_length=16)

    @model_validator(mode='after')
    def derive_status(self):
        ids = [unit.unit_id for unit in self.units]
        if len(ids) != len(set(ids)):
            raise ValueError('Duplicate reader delivery unit')
        if any(f.action == 'reselect' for u in self.units for f in u.findings):
            self.status = 'reselect'
        elif any(u.status == 'unverified' for u in self.units):
            self.status = 'unverified'
        elif any(u.status == 'gap' for u in self.units):
            self.status = 'revise'
        else:
            self.status = 'pass'
        return self


def _object(value):
    return value.model_dump(mode='json') if hasattr(value, 'model_dump') else value if isinstance(value, dict) else {}


def _intent(task, concept):
    from .creative_exploration import active_creative_requirements
    fresh = ' '.join(str(task.get(k, '') or '') for k in ('goal', 'scene')) + ' ' + active_creative_requirements(task)
    # An explicitly rejected form is not a requested tutorial/tradeoff.
    fresh = re.sub(r'(?:不需要|不要|不做|不是|不写|无需)[^，。；\n]{0,8}(?:教程|步骤|选购|清单)', '', fresh)
    if re.search(r'教程|操作步骤|如何操作|怎么操作|教会|传照片|传文件|排查', fresh):
        return 'tutorial', 'task'
    if re.search(r'怎么选|如何选|选购|取舍|对比|购买建议', fresh):
        return 'buying_tradeoff', 'task'
    if re.search(r'情绪|审美|氛围|心情|意境|留白|情感|配色', fresh):
        return 'aesthetic_emotion', 'task'
    if re.search(r'到店体验|体验想法|试一试|来试|试拍|体验方案', fresh):
        return 'local_experience', 'task'
    mechanism = concept.get('mechanism')
    if mechanism in {'emotional_scene', 'cultural_connection'}:
        return 'aesthetic_emotion', 'selected_concept'
    if mechanism == 'practical_demo':
        return 'local_experience', 'selected_concept'
    if mechanism in {'detail_reveal', 'contrast', 'humor'} or re.search(r'观察|生活细节|日常|发现', fresh):
        return 'everyday_observation', 'selected_concept' if mechanism else 'task'
    return 'open_expression', 'task'


def reader_intent(task, selected_concept=None):
    """Intent projection for existing editorial rules; not a quality verdict."""
    return _intent(_object(task), _object(selected_concept))[0]


def build_reader_contract(task, draft, *, selected_concept=None, edit_scope=None):
    """Server projection; generated draft cannot choose a lower standard."""
    from .marketing_surfaces import marketing_public_surfaces
    from .creative_exploration import active_creative_requirements
    task, draft, concept = _object(task), _object(draft), _object(selected_concept)
    surfaces = marketing_public_surfaces(draft)
    actual = {key: text for key, text in surfaces.items() if not key.startswith('variant:')}
    intent, source = _intent(task, concept)
    units = {'main_post': list(actual)}
    cards = [key for key in actual if key.startswith('card:')]
    if cards:
        units['card_copy'] = cards
    scope = None
    if isinstance(edit_scope, dict):
        allowed = edit_scope.get('allowed_surfaces')
        if not isinstance(allowed, list) or not allowed or any(not isinstance(k, str) or k not in actual for k in allowed):
            raise ValueError('Invalid trusted reader edit scope')
        scope = {'allowed_surfaces': list(dict.fromkeys(allowed)),
                 'instruction': str(edit_scope.get('instruction', ''))[:2000]}
    contract = {'version': 'reader-value-v2', 'intent': intent, 'intent_basis': source,
        'rubric': RUBRICS[intent], 'goal': str(task.get('goal', '') or '')[:800],
        'promoted_product': str(task.get('product', '') or '')[:200],
        'subject_alignment_standard': SUBJECT_ALIGNMENT_STANDARD,
        'current_request': active_creative_requirements(task)[:2200],
        'selected_payoff': str(concept.get('payoff', '') or '')[:600],
        'selected_angle': str(concept.get('angle', '') or '')[:600],
        'units': units, 'edit_scope': scope,
        'delivery_basis': 'public_text_and_card_copy_only; not rendered-image certification',
        'draft_digest': hashlib.sha256(json.dumps(actual, ensure_ascii=False, sort_keys=True).encode()).hexdigest()}
    contract['contract_id'] = hashlib.sha256(json.dumps(contract, ensure_ascii=False, sort_keys=True).encode()).hexdigest()
    return contract


READER_VALUE_INSTRUCTION = '''
reader_value_contract存在时，复用本次独立审校按该契约返回reader_value：contract_id、intent、units、preserve。
units逐一覆盖契约units，unit_id一致；每项包含status=met/gap/unverified、promise实际标题承诺、payoff实际回报、first_screen、evidence、findings。
每条evidence及preserve为{region:精确公开字段ID,quote:""}，服务器补真实原文。met必须给实际兑现证据，不引用内部策略/模型自述，不用标题或CTA代替教程/取舍答案。
findings只列影响承诺兑现的重要缺陷：{region,quote:"",kind:unfulfilled_promise/missing_payoff/surface_mismatch/unusable_action/unrelated_brand/subject_ambiguity,reason,target具体修订目标,action:revise/reselect,repair_surfaces:[精确字段ID],related_evidence:[]}。
必须按subject_alignment_standard检查标题/首屏的评价主体与正文营销对象是否可辨，不能只因正文有真实规格就判通过。负面痛点、旧新对比、真实缺点、悬念和双关不自动构成问题；不要求正向广告。只有重要误归风险才报subject_ambiguity，主region定位引发歧义的标题或首屏，related_evidence至少再引用一个不同的真实正文/图卡字段，说明当前指代为何不能分清；修订只澄清主体，保留原痛点和语气。已就近明确旧设备与新产品的内容应通过，不强制教程或免责声明。
不要为了证明审核存在找问题：措辞偏好、另一个审美、缺地名或缺CTA本身不构成缺陷。审美/情绪/观察可由具体意象、态度、关系兑现，不强行列步骤/参数。
主帖可由正文与图卡共同交付；card_copy判断这组图卡的实际承诺，封面可只做钩子，不要求每一张都讲完教程。互斥标题候选不拼成一篇。
纯文字/分镜审查不能声称完整图像已生成或看过像素；图片存在与实际像素另由真实工具验收。
preserve精确指出已有创意资产。只修重要且定位的问题，不能把新卖点、新回报或补免责声明当通用修法。
edit_scope若存在，repair_surfaces必须遵守其授权字段；确需范围外改动时在target说清冲突并保留原稿，不能声称已经改好，不能自行扩大范围。
不返回分数、流量或转化率；最终判定由程序从逐项结果派生，不能用总分抵消缺项。
'''


def validate_reader_value(raw, contract, surfaces):
    """Resolve only exact public fields; no fuzzy quote or semantic pass repair."""
    value = deepcopy(raw)
    if not isinstance(value, dict):
        raise ValueError('missing_reader_value_review')
    if value.get('contract_id') != contract['contract_id'] or value.get('intent') != contract['intent']:
        raise ValueError('stale_or_changed_reader_value_contract')
    from .marketing_surfaces import resolve_surface_finding
    units = contract['units']
    rows = value.get('units')
    if not isinstance(rows, list):
        raise ValueError('missing_reader_delivery_units')
    scope_conflicts = set()
    edit_scope = contract.get('edit_scope')
    for row in rows:
        if not isinstance(row, dict) or row.get('unit_id') not in units:
            raise ValueError('unknown_reader_delivery_unit')
        permitted = set(units[row['unit_id']])
        for key in ('evidence', 'findings'):
            for item in row.get(key, []) if isinstance(row.get(key), list) else []:
                if not isinstance(item, dict) or item.get('region') not in permitted:
                    raise ValueError('reader_evidence_outside_delivery_unit')
                found = resolve_surface_finding(item, surfaces, allow_visual=False)
                item['quote'] = found['quote']
                if key == 'evidence' and not item['quote'].strip():
                    raise ValueError('empty_reader_payoff_evidence')
                if key == 'findings':
                    for related in item.get('related_evidence', []):
                        if not isinstance(related, dict) or related.get('region') not in permitted:
                            raise ValueError('reader_subject_evidence_outside_delivery_unit')
                        related['quote'] = resolve_surface_finding(related, surfaces, allow_visual=False)['quote']
                        if not related['quote'].strip():
                            raise ValueError('empty_reader_subject_evidence')
                    targets = item.get('repair_surfaces', [])
                    if any(target not in units['main_post'] for target in targets):
                        raise ValueError('unknown_reader_repair_surface')
                    if edit_scope:
                        scope_conflicts.update(target for target in targets if target not in edit_scope['allowed_surfaces'])
    for item in value.get('preserve', []) if isinstance(value.get('preserve'), list) else []:
        if not isinstance(item, dict) or item.get('region') not in units['main_post']:
            raise ValueError('unknown_reader_preserve_surface')
        item['quote'] = resolve_surface_finding(item, surfaces, allow_visual=False)['quote']
        if not item['quote'].strip():
            raise ValueError('empty_reader_preserve_asset')
    value['scope_conflicts'] = sorted(scope_conflicts)
    parsed = ReaderValueReview.model_validate(value)
    if {row.unit_id for row in parsed.units} != set(units):
        raise ValueError('incomplete_reader_delivery_review')
    # For task/method promises, titles and action labels alone cannot certify an answer.
    if contract['intent'] in {'tutorial', 'buying_tradeoff'}:
        for unit in parsed.units:
            if unit.status == 'met' and not any(c.region.startswith(('body:', 'card:')) or c.region in {'body', 'text'} for c in unit.evidence):
                raise ValueError('title_or_cta_is_not_reader_answer')
    return parsed.model_dump(mode='json')


def reader_revision_brief(review, contract=None):
    """Specific repair information for the existing bounded generation loop."""
    parsed = ReaderValueReview.model_validate(review)
    if parsed.status == 'pass':
        return ''
    rows = []
    scope = (contract or {}).get('edit_scope') or {}
    if scope:
        rows.append('本轮只可修改：' + '、'.join(scope['allowed_surfaces']) + '；其余文字、图片和事实保持。')
    if parsed.scope_conflicts:
        rows.append('所需修订还涉及范围外字段：' + '、'.join(parsed.scope_conflicts) + '；本轮不改动这些字段，保留原稿，需明确扩大修改范围。')
    for unit in parsed.units:
        for finding in unit.findings:
            context = ('；对应正文：' + '；'.join(f'{item.region}「{item.quote[:180]}」' for item in finding.related_evidence)) if finding.related_evidence else ''
            rows.append(f'{finding.region}：{finding.reason}{context}；修订目标：{finding.target}；仅处理：' + '、'.join(finding.repair_surfaces))
    if parsed.preserve:
        rows.append('必须保留：' + '；'.join(f'{item.region}「{item.quote[:120]}」' for item in parsed.preserve))
    return '\n'.join(rows)[:2800]
