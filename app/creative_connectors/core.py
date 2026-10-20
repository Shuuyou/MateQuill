"""Platform editorial connectors: local composition, never network or publishing."""
from __future__ import annotations
from copy import deepcopy
import json
from pathlib import Path
from typing import Any, Protocol, runtime_checkable
from pydantic import ValidationError
from ..models import AuditItem
from .models import CreativePlan

REGISTRY_DATA = json.loads(Path(__file__).with_name('strategies.v1.json').read_text(encoding='utf-8-sig'))
PLATFORM_PLAYBOOKS = deepcopy(REGISTRY_DATA['platform_profiles'])
VERSION = REGISTRY_DATA['connector_version']


def _get(value, key, default=None):
    return value.get(key, default) if isinstance(value, dict) else getattr(value, key, default)


@runtime_checkable
class CreativeConnector(Protocol):
    platform: str
    connector_id: str
    version: str
    def describe(self) -> dict: ...
    def build_brief(self, task, bundle, strategy) -> dict: ...
    def normalize_plan(self, raw_plan, task, bundle) -> dict: ...
    def review(self, plan, result, bundle) -> list[AuditItem]: ...


class EditorialConnector:
    """Data-driven implementation; subclasses or explicit registrations can extend it."""
    def __init__(self, platform: str, name: str, *, style_id: str = '',
                 connector_id: str = '', version: str = VERSION, profile: dict | None = None,
                 playbooks: list[dict] | None = None):
        self.platform = platform
        self.name = name
        self.connector_id = connector_id or f'{platform}.editorial.v1'
        self.version = version
        self.style_id = style_id
        self.profile = deepcopy(profile if profile is not None else PLATFORM_PLAYBOOKS[platform])
        self.playbooks = deepcopy(playbooks if playbooks is not None else [
            p for p in REGISTRY_DATA['playbooks'] if p['platform'] == platform])
        if style_id and style_id not in {p['id'] for p in self.playbooks}:
            raise ValueError('Unknown creative style for platform')

    def describe(self):
        return {'id': self.connector_id, 'platform': self.platform, 'version': self.version,
                'name': self.name, 'styles': [{'id': p['id'], 'name': p.get('title') or p.get('headline') or p['id']}
                                              for p in self.playbooks],
                'capabilities': ['brief', 'storyboard', 'evidence_review', 'test_variants'],
                'publishing': False, 'network_required': False,
                'effect_claim': '编辑策略与测试候选，不承诺流量或预测爆款'}

    def _select(self, task):
        requested_style = self.style_id or _get(task, 'creative_style', 'auto')
        if requested_style and requested_style != 'auto':
            match = next((p for p in self.playbooks if p['id'] == requested_style), None)
            if match is None:
                raise ValueError('Unknown creative style for platform')
            return match
        text = ' '.join(str(_get(task, k, '')) for k in ('audience','scene','goal','requirements'))
        if self.platform == 'xiaohongshu':
            signals = [('xhs_library_flow', ('图书馆','论文','截图','资料整理','整理资料')),
                       ('xhs_final_file', ('设计','交稿','导出','字体','剪辑')),
                       ('xhs_travel_cable', ('差旅','会议','投屏','出差','商务')),
                       ('xhs_course_list', ('新生','课程','上课','课表'))]
            for book_id, terms in signals:
                if any(term in text for term in terms):
                    return next(p for p in self.playbooks if p['id'] == book_id)
        if self.platform != 'xiaohongshu' and self.playbooks:
            return self.playbooks[0]
        return {'id': f'{self.platform}_task_first', 'platform': self.platform,
                'device': '任务冲突、清单兑现、结尾回扣',
                'promise': '把用户输入的具体任务转成有依据的核对方法',
                'cards': [{'role': 'cover', 'headline': '用你的任务，提出具体选择问题'},
                          {'role': 'tension', 'headline': '把卡点拆成需要核对的条件'},
                          {'role': 'method', 'headline': '给出可执行的核对步骤'},
                          {'role': 'evidence', 'headline': '已知与待实测，分别列清'},
                          {'role': 'summary', 'headline': '留下能保存的任务清单'}]}

    def build_brief(self, task, bundle, strategy=None):
        book = deepcopy(self._select(task))
        platform_rule = deepcopy(self.profile.get('platform_rule') or REGISTRY_DATA.get('platform_rules',{}).get(self.platform,{}))
        required = {'name','min_images','max_images','structure','tone','cta'}
        if not isinstance(platform_rule,dict) or not required.issubset(platform_rule):
            raise ValueError('Connector must declare complete platform_rule')
        if not isinstance(platform_rule['min_images'],int) or not isinstance(platform_rule['max_images'],int) or not 0 <= platform_rule['min_images'] <= platform_rule['max_images'] <= 12:
            raise ValueError('Invalid connector image limits')
        schema = CreativePlan.model_json_schema()
        limits = REGISTRY_DATA.get('card_limits',{}).get(self.platform, {'min':1,'max':12})
        schema['properties']['cards'].update(minItems=limits['min'], maxItems=limits['max'])
        # Examples are composition references, never factual audience biographies.
        return {**self.describe(), 'fact_budget': self.profile['fact_budget'], 'platform_rule': platform_rule,
                'playbook': deepcopy(self.profile), 'creative_playbook': book,
                'task_context': {k: _get(task, k, '') for k in ('product','audience','scene','stage','goal','requirements')},
                'adaptation': '按task_context重写示例主题与动作；研究样例不是用户经历，不照搬不相符的课表、转接线或最终版。',
                'fact_selection': [fid for fid in (_get(strategy or {}, 'priority_fact_ids', []) or []) if fid in {_get(f,'id') for f in _get(bundle,'facts',[])}][:self.profile['fact_budget']],
                'asset_policy': {'available_asset_ids': [_get(a,'id') for a in _get(bundle,'assets',[])],
                                 'text_cards_allowed': True, 'force_nine_images': False,
                                 'truth_statuses': ['official_material','real_photo','illustrative','diagram','pending_capture','hypothetical'],
                                 'no_asset_fallback': '使用明确标注的任务卡/文字卡；不得编造实拍、软件截图或任意引用其他型号'},
                'narrative_rules': ['标题交代对象与阅读收益，封面补充一个具体动作或冲突',
                                    '每页只完成一个信息任务，伏笔对应promise_payoff中的实际兑现页',
                                    '无记录不编造亲测、顾客聊天、用户口碑、效果数字',
                                    'test_variants是固定事实底稿的候选，不含预测CTR或爆款概率'],
                'design_defaults': deepcopy(REGISTRY_DATA['design_defaults']),
                'output_schema': schema, 'card_limits': limits,
                'evidence_boundary': '事实与素材ID仅可取自本次证据；条件设想须披露，示意图不能证明功能。'}

    def normalize_plan(self, raw_plan, task=None, bundle=None):
        if not raw_plan:
            return {}
        if isinstance(raw_plan, CreativePlan):
            raw_plan = raw_plan.model_dump()
        # Force service-selected metadata before validation; never trust model values.
        if not isinstance(raw_plan, dict):
            raise ValueError('Creative plan must be an object')
        data = deepcopy(raw_plan)
        data.update(connector_id=self.connector_id, connector_version=self.version, platform=self.platform)
        return CreativePlan.model_validate(data).model_dump()

    def review(self, plan, result=None, bundle=None):
        notes = []
        def add(severity, code, message):
            notes.append(AuditItem(severity=severity, category='creative_' + code, message=message))
        if not plan:
            add('warning', 'missing', '尚未生成平台创意分镜，不能将普通图文输出视为已完成创意设计。')
            return notes
        try:
            raw = plan.model_dump() if isinstance(plan, CreativePlan) else plan
            valid = CreativePlan.model_validate(raw)
        except (ValidationError, TypeError, ValueError):
            add('error', 'schema', '创意计划结构无效，请重新生成；不得跳过分镜校验。')
            return notes
        if (valid.connector_id, valid.connector_version, valid.platform) != (self.connector_id,self.version,self.platform):
            add('error', 'metadata', '创意计划连接器、版本或平台与当前选择不一致。')
        facts = {_get(f,'id') for f in _get(bundle,'facts',[])}
        assets = {_get(a,'id'): a for a in _get(bundle,'assets',[])}
        ids = [c.id for c in valid.cards]
        if len(ids) != len(set(ids)):
            add('error', 'duplicate_card', '分镜页ID重复，无法可靠定位承诺兑现页。')
        fingerprints = set()
        for card in valid.cards:
            unknown_facts = set(card.fact_ids)-facts
            unknown_assets = set(card.asset_ids)-assets.keys()
            if unknown_facts:
                add('error', 'fact_reference', f'{card.id}引用了本次证据中不存在的事实ID。')
            if unknown_assets:
                add('error', 'asset_reference', f'{card.id}引用了本次证据中不存在的素材ID。')
            if len(card.asset_ids) != len(set(card.asset_ids)) or len(card.fact_ids) != len(set(card.fact_ids)):
                add('error', 'duplicate_reference', f'{card.id}存在重复素材或事实引用。')
            signature = (card.headline.strip(),card.body.strip())
            if any(signature) and signature in fingerprints:
                add('warning', 'repeated_information', f'{card.id}与前页文字重复，请给每页独立信息任务。')
            fingerprints.add(signature)
            if not card.visual_intent:
                add('warning', 'visual_intent', f'{card.id}缺少画面任务说明，需明确它与正文的关系。')
            if card.truth_status not in ('official_material','real_photo','illustrative','diagram','pending_capture','hypothetical'):
                add('error', 'truth_status', f'{card.id}未明确区分官方素材、真实实拍或示意。')
            if card.truth_status in ('real_photo','真实实拍','用户提供'):
                if not card.asset_ids:
                    add('error', 'photo_truth', f'{card.id}声称真实实拍但没有对应素材。')
                for aid in card.asset_ids:
                    asset = assets.get(aid)
                    if asset and _get(asset,'provenance','') not in ('user_photo','authorized_photo','real_photo','store_upload'):
                        add('error', 'photo_truth', f'{card.id}将未核实的来源素材标为真实实拍。')
            if card.block_index is not None and result is not None:
                if card.block_index >= len(_get(result,'blocks',[])):
                    add('error', 'block_reference', f'{card.id}指向不存在的正文段落。')
        for promise in valid.promise_payoff:
            if set(promise.payoff_card_ids)-set(ids):
                add('error', 'payoff_reference', '阅读承诺指向不存在的兑现页。')
            elif promise.promise and not promise.payoff_card_ids:
                add('error', 'unfulfilled_promise', '阅读承诺没有指定兑现页，不能只制造悬念。')
        if not valid.cards:
            add('warning', 'no_cards', '创意计划没有分镜页，请补充标题封面与内容节奏。')
        if not valid.reader_gain or not valid.reader_tension:
            add('warning', 'reader_value', '请明确一个读者阻碍和读完可完成的具体动作。')
        if len(valid.test_variants) < 2:
            add('warning', 'variants', '建议提供至少两组标题封面测试候选，不预测点击率。')
        if self.platform == 'poster' and len(valid.cards) > 1:
            add('error', 'poster_pages', '海报默认单屏，多个分镜应作为备选方案而非拼成长图。')
        return notes


class XiaohongshuConnector(EditorialConnector):
    def __init__(self, **kwargs): super().__init__('xiaohongshu','小红书图文叙事',**kwargs)
class WechatConnector(EditorialConnector):
    def __init__(self, **kwargs): super().__init__('wechat','公众号决策叙事',**kwargs)
class MomentsConnector(EditorialConnector):
    def __init__(self, **kwargs): super().__init__('moments','朋友圈有用提醒',**kwargs)
class PosterConnector(EditorialConnector):
    def __init__(self, **kwargs): super().__init__('poster','单屏任务海报',**kwargs)

_REGISTRY: dict[str, CreativeConnector] = {}
_DEFAULTS: dict[str,str] = {}


def register_connector(connector: CreativeConnector, *, default: bool = False, replace: bool = False):
    """Explicit application registration only; IDs never load code or make requests."""
    if not isinstance(connector, CreativeConnector):
        raise TypeError('Connector must implement CreativeConnector')
    if connector.connector_id in _REGISTRY and not replace:
        raise ValueError('Connector ID already registered')
    _REGISTRY[connector.connector_id] = connector
    if default or connector.platform not in _DEFAULTS:
        _DEFAULTS[connector.platform] = connector.connector_id


def list_connectors() -> list[dict]:
    return [deepcopy(c.describe()) for c in _REGISTRY.values()]


def get_connector(platform: str, connector_id: str = 'auto') -> CreativeConnector:
    if platform not in _DEFAULTS:
        raise ValueError('Unknown creative platform')
    if not connector_id or connector_id == 'auto':
        return _REGISTRY[_DEFAULTS[platform]]
    if connector_id in _REGISTRY:
        connector = _REGISTRY[connector_id]
        if connector.platform != platform:
            raise ValueError('Connector platform mismatch')
        return connector
    # Built-in styles are data keys, not module names, URLs or executable paths.
    base = _REGISTRY[_DEFAULTS[platform]]
    if isinstance(base, EditorialConnector) and connector_id in {p['id'] for p in base.playbooks}:
        return EditorialConnector(platform,base.name,style_id=connector_id,
                                  connector_id=base.connector_id,version=base.version,
                                  profile=base.profile,playbooks=base.playbooks)
    raise ValueError('Unknown connector or creative style')


for _connector_class in (XiaohongshuConnector, WechatConnector, MomentsConnector, PosterConnector):
    register_connector(_connector_class(), default=True)
