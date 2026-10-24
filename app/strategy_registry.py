"""Versioned editorial strategies with tenant-scoped immutable execution evidence.

Strategies contain methods, never product facts or purported customer histories.
Resolution is deterministic. Feedback is observed data, not an effect forecast.
"""
from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import json
from typing import Any, Literal
from uuid import uuid4

from fastapi import HTTPException
from pydantic import BaseModel, ConfigDict, Field, model_validator

from .tenancy.context import get_context, require_context, authorize_scope
from .tenancy.resources import authorize_resource, register_resource, list_resources


class StrategyPack(BaseModel):
    model_config = ConfigDict(extra='forbid')
    id: str = Field(pattern=r'^[a-z][a-z0-9_.-]{2,99}$')
    version: str = Field(pattern=r'^\d+\.\d+\.\d+$')
    title: str = Field(min_length=1, max_length=160)
    status: Literal['draft', 'active', 'retired'] = 'draft'
    platforms: list[str] = Field(min_length=1, max_length=12)
    publishers: list[str] = Field(default_factory=list, max_length=12)
    audiences: list[str] = Field(default_factory=list, max_length=30)
    objectives: list[str] = Field(default_factory=list, max_length=20)
    required_materials: list[str] = Field(default_factory=list, max_length=12)
    valid_from: datetime | None = None
    expires_at: datetime | None = None
    recipe: dict[str, Any] = Field(default_factory=dict)
    sources: list[dict[str, str]] = Field(default_factory=list, max_length=30)
    evaluation: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode='after')
    def validity(self):
        for date in (self.valid_from, self.expires_at):
            if date is not None and date.tzinfo is None:
                raise ValueError('Strategy dates must include a timezone')
        if self.valid_from and self.expires_at and self.valid_from >= self.expires_at:
            raise ValueError('Strategy validity interval is empty')
        if not self.recipe.get('narrative') or not self.recipe.get('visual_sequence'):
            raise ValueError('Strategy needs narrative and visual_sequence')
        return self


def _pack(identifier, title, publishers, narrative, visuals, **extra):
    platforms = extra.pop('platforms', ['xiaohongshu', 'wechat', 'moments', 'poster'])
    return StrategyPack(id=identifier, version='1.1.0', status='active', title=title,
        platforms=platforms, publishers=publishers,
        recipe={'narrative': narrative, 'visual_sequence': visuals,
                'truth_rule': '仅用本次事实和素材；没有真实经历时采用演示或问题解答表达。'}, **extra).model_dump(mode='json')


BUILTIN_PACKS = [
    _pack('retail.task_answer', '从一个具体问题讲清产品', [],
          ['一个让人想了解的反差或使用愿望', '用具体动作展开已支持的产品价值', '解释开头的细节并兑现收益', '给出一个自然的行动邀请'],
          ['问题与设备同框', '操作过程或文字步骤', '结果与条件']),
    _pack('retail.real_demo', '本店实机演示', ['store', 'salesperson'],
          ['本次真实照片里的操作对象', '一个任务的实际步骤', '对应产品事实与条件', '邀请体验这项任务'],
          ['实机照片作封面', '同一任务的操作细节', '核实后的结果'], required_materials=['real_photo']),
    _pack('retail.staff_observation', '店员的一项产品观察', ['salesperson'],
          ['一个具体问题或观察', '店员如何解释或演示', '事实支持与适用条件', '用口语邀请继续提问'],
          ['设备细节或真实现场', '关键操作', '简洁的问题解答']),
    _pack('retail.store_experience', '门店的具体体验邀请', ['store'],
          ['一项值得来体验的任务', '展示产品如何参与任务', '本店已确认的服务', '自然的体验邀请'],
          ['本店素材或官方产品图', '任务分步演示', '确认后的到店信息']),
    _pack('retail.regional_campaign', '区域活动与体验串联', ['regional_agent', 'distributor', 'provincial_agent'],
          ['来源明确的活动主题', '适合目标人群的体验任务', '参与方式与适用门店', '有依据的行动入口'],
          ['活动主题与真实产品', '体验任务', '门店或参与步骤']),
    _pack('retail.brand_launch', '新品能力与生活场景', ['brand', 'official'],
          ['新品一个有吸引力的设计或体验线索', '用具体场景放大这项设计或能力的价值', '用官方细节兑现开头的好奇', '官方活动或体验入口'],
          ['官方产品视觉', '场景与功能细节', '信息清晰的行动页']),
    _pack('retail.ecosystem_task', '华为生态完成一个任务', [],
          ['跨设备任务里的具体阻碍', '按顺序展示设备如何协作', '核实型号与兼容条件', '邀请带着自己的任务体验'],
          ['相关设备同框或明确的设备示意', '连续任务步骤', '完成结果与适配条件'],
          objectives=['ecosystem', '生态', '协同', '跨设备']),
    _pack('xhs.curiosity_payoff', '一个细节，让人想继续看', ['store', 'salesperson'],
          ['从素材里挑一个反常识细节或未说完的问题', '封面与标题分别提供线索，避免重复',
           '用真实操作和已核实事实解释这个细节', '最后兑现开头的问题，留一个值得讨论的选择'],
          ['设备局部或具体动作作封面，不堆参数', '放大关键线索', '操作或事实解释', '兑现结果或可保存的方法'],
          platforms=['xiaohongshu']),
    _pack('moments.familiar_answer', '熟客的一句实用答复', ['store', 'salesperson'],
          ['从已购设备或明确问题开场', '口语说明一个有用的做法', '交代必要适配条件',
           '用一句自然的答疑邀请收尾，不重复促销'],
          ['一张相关实机或操作图', '必要时补一张结果图，文字不铺满画面'],
          platforms=['moments'], audiences=['熟客', '老客户', '已购']),
]


def _digest(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
                                   separators=(',', ':')).encode()).hexdigest()


def _now(value=None):
    date = value or datetime.now(timezone.utc)
    if date.tzinfo is None:
        raise ValueError('Resolution time must include a timezone')
    return date


def list_strategy_packs(*, include_inactive=False):
    packs = deepcopy(BUILTIN_PACKS)
    if get_context() is not None:
        packs.extend(row['body'] for row in list_resources('strategy_pack') if row.get('body'))
    # Local versions remain tenant-scoped; resolution compares eligible versions deterministically.
    return [p for p in packs if include_inactive or p['status'] == 'active']


def evaluate_strategy_pack(value):
    pack = StrategyPack.model_validate(value)
    recipe = pack.recipe
    checks = {'narrative': bool(recipe.get('narrative')),
              'visual_sequence': bool(recipe.get('visual_sequence')),
              'truth_rule': bool(recipe.get('truth_rule')),
              'no_product_facts': not any(k in recipe for k in ('product_facts', 'prices', 'stock'))}
    return {'kind': 'structural', 'passed': all(checks.values()), 'checks': checks,
            'scope': '结构与事实边界检查，不是营销效果评估'}


def save_strategy_pack(value, *, store_id=None, visibility='tenant'):
    context = require_context()
    authorize_scope(store_id, 'manage')
    pack = StrategyPack.model_validate(value).model_dump(mode='json')
    pack['evaluation'] = evaluate_strategy_pack(pack)
    if pack['status'] == 'active' and not pack['evaluation']['passed']:
        raise ValueError('Active strategy must pass structural evaluation')
    identifier = _digest([context.tenant_id, pack['id'], pack['version']])
    # Versions are immutable even for their owner. Publish a new version to revise.
    existing = next((row for row in list_resources('strategy_pack') if row['id'] == identifier), None)
    if existing and existing['body'] != pack:
        raise HTTPException(409, '该策略版本已存在，请创建新版本')
    return register_resource('strategy_pack', identifier, store_id, visibility, pack)


def _context(task, bundle=None, strategy=None):
    def get(value, key, default=''):
        return value.get(key, default) if isinstance(value, dict) else getattr(value, key, default)
    context = {key: get(task, key) for key in ('platform', 'product', 'audience', 'scene', 'goal', 'requirements', 'store_id')}
    context['ecosystem_products'] = list(get(task, 'ecosystem_products', []) or [])
    requirements = context.get('requirements')
    if isinstance(requirements, str):
        try:
            requirements = json.loads(requirements)
        except (ValueError, TypeError):
            requirements = {}
    if isinstance(requirements, dict):
        campaign = requirements.get('campaign', requirements)
        if isinstance(campaign, dict):
            context.update({k: campaign[k] for k in ('publisher_identity', 'objective') if k in campaign})
    supplied = get(strategy or {}, 'strategy_context', {})
    if isinstance(supplied, dict):
        context.update(supplied)
    assets = get(bundle or {}, 'assets', [])
    context['materials'] = sorted((set(context.get('materials', [])) - {'real_photo'}) | {
        'real_photo' for asset in assets
        if get(asset, 'provenance') in ('user_photo', 'authorized_photo', 'real_photo', 'store_upload')})
    if get(task, 'ecosystem_products', []):
        context.setdefault('objective', 'ecosystem')
    context.setdefault('objective', context.get('goal', ''))
    context.setdefault('publisher_identity', 'store' if context.get('store_id') else 'unknown')
    return context


def resolve_strategy(context, *, now=None, requested_id='auto', packs=None):
    clock = _now(now)
    eligible = []
    for raw in packs if packs is not None else list_strategy_packs():
        pack = StrategyPack.model_validate(raw)
        if pack.status != 'active' or context.get('platform') not in pack.platforms:
            continue
        if pack.valid_from and clock < pack.valid_from or pack.expires_at and clock >= pack.expires_at:
            continue
        if pack.publishers and context.get('publisher_identity') not in pack.publishers:
            continue
        if not set(pack.required_materials).issubset(context.get('materials', [])):
            continue
        objective = str(context.get('objective', ''))
        audience = str(context.get('audience', ''))
        if pack.objectives and not any(term in objective for term in pack.objectives):
            continue
        if pack.audiences and not any(term in audience for term in pack.audiences):
            continue
        if requested_id != 'auto' and requested_id != pack.id:
            continue
        score = (10 * bool(pack.publishers) + 20 * bool(pack.objectives)
                 + 15 * bool(pack.required_materials) + 5 * bool(pack.audiences)
                 + 3 * (len(pack.platforms) == 1))
        eligible.append((score, tuple(int(v) for v in pack.version.split('.')), pack.id, pack.model_dump(mode='json')))
    if not eligible:
        raise ValueError('No eligible strategy for this context')
    eligible.sort(key=lambda item: (item[0], item[1], item[2]), reverse=True)
    score, _, _, selected = eligible[0]
    # Context is frozen with the recipe so replay does not consult current registry state.
    resolved = {'schema_version': 1, 'pack': selected, 'context': deepcopy(context),
                'reason': {'score': score, 'eligible_count': len(eligible),
                           'publisher': context.get('publisher_identity', 'unknown')},
                'resolved_at': clock.isoformat(), 'effect_prediction': None}
    return {**resolved, 'snapshot_hash': _digest(resolved)}


def strategy_for_task(task, bundle=None, strategy=None):
    context = _context(task, bundle, strategy)
    snapshot = (strategy or {}).get('strategy_snapshot') if isinstance(strategy, dict) else None
    if snapshot:
        validated = validate_snapshot(snapshot)
        frozen = validated['context']
        for key in ('platform', 'product', 'publisher_identity', 'store_id', 'ecosystem_products'):
            if frozen.get(key, [] if key == 'ecosystem_products' else '') != context.get(key, [] if key == 'ecosystem_products' else ''):
                raise ValueError(f'Strategy snapshot context mismatch: {key}')
        return validated
    return resolve_strategy(context)


def validate_snapshot(snapshot):
    data = deepcopy(snapshot)
    supplied_hash = data.pop('snapshot_hash', '')
    if not supplied_hash or supplied_hash != _digest(data):
        raise ValueError('Strategy snapshot integrity check failed')
    StrategyPack.model_validate(data['pack'])
    return {**data, 'snapshot_hash': supplied_hash}


def freeze_strategy_snapshot(snapshot, *, run_id, store_id=None, assignment_id=''):
    require_context()
    run = authorize_resource('run', run_id)
    store = run.get('store_id')
    if store_id is not None and store_id != store:
        raise HTTPException(404, '没有找到这家门店的运行记录')
    if assignment_id:
        assignment = authorize_resource('channel_assignment', assignment_id)
        if assignment.get('store_id') != store:
            raise HTTPException(404, '任务与运行门店不一致')
        run_body = run.get('body') or {}
        run_task = run_body.get('_poster_task') or run_body.get('task') or {}
        if run_task.get('assignment_id') != assignment_id:
            raise HTTPException(404, '运行记录不属于该任务')
    body = {'run_id': run_id, 'assignment_id': assignment_id, 'snapshot': validate_snapshot(snapshot)}
    identifier = _digest([require_context().tenant_id, run_id, body['snapshot']['snapshot_hash']])
    return register_resource('strategy_snapshot', identifier, store, 'private', body)


class StrategyFeedback(BaseModel):
    model_config = ConfigDict(extra='forbid')
    event: Literal['adopted', 'edited', 'rejected', 'exported', 'published', 'performance']
    note: str = Field(default='', max_length=2000)
    active_input_seconds: float | None = Field(default=None, ge=0, le=86400)
    action_count: int | None = Field(default=None, ge=0, le=100000)
    impressions: int | None = Field(default=None, ge=0)
    inquiries: int | None = Field(default=None, ge=0)
    publication_id: str = Field(default='', max_length=300)
    source: Literal['user_report', 'authorized_platform', 'app_event'] = 'user_report'

    @model_validator(mode='after')
    def event_evidence(self):
        if self.event in ('published', 'performance') and not self.publication_id:
            raise ValueError('Publication reference required for publication or performance feedback')
        if self.event != 'performance' and (self.impressions is not None or self.inquiries is not None):
            raise ValueError('Performance metrics require a performance event')
        return self


def record_strategy_feedback(snapshot_id, value, *, source='user_report'):
    """``source`` is a server-selected channel, never an HTTP body field."""
    context = require_context()
    snapshot = authorize_resource('strategy_snapshot', snapshot_id)
    body = snapshot['body']
    run = authorize_resource('run', body['run_id'])
    if snapshot.get('store_id') != run.get('store_id'):
        raise HTTPException(404, '运行记录与策略门店不一致')
    if body.get('assignment_id'):
        assignment = authorize_resource('channel_assignment', body['assignment_id'])
        if assignment.get('store_id') != run.get('store_id'):
            raise HTTPException(404, '任务与运行门店不一致')
        run_body = run.get('body') or {}
        run_task = run_body.get('_poster_task') or run_body.get('task') or {}
        if run_task.get('assignment_id') != body['assignment_id']:
            raise HTTPException(404, '运行记录不属于该任务')
    event = StrategyFeedback.model_validate({**value, 'source': source}).model_dump(mode='json')
    if event['event'] in ('published', 'performance'):
        publication = authorize_resource('channel_publication', event['publication_id'])
        if publication.get('store_id') != run.get('store_id') or (publication.get('body') or {}).get('run_id') != body['run_id']:
            raise HTTPException(404, '发布记录不属于本次内容')
    event.update(snapshot_id=snapshot_id, run_id=body['run_id'], actor_id=context.user_id,
                 snapshot_hash=body['snapshot']['snapshot_hash'], recorded_at=_now().isoformat())
    return register_resource('strategy_feedback', uuid4().hex, run.get('store_id'), 'private', event)


def record_workflow_event(run_id, event, *, publication_id=''):
    """Capture actual user actions without an additional feedback form."""
    context = get_context()
    if context is None or context.is_legacy:
        return None
    run = authorize_resource('run', run_id)
    identifier = (run.get('body') or {}).get('strategy', {}).get('snapshot_id')
    if not identifier:
        return None
    try:
        authorize_resource('strategy_snapshot', identifier)
    except HTTPException as exc:
        if exc.status_code == 404:
            return None  # sharing a draft does not share its author's private memory
        raise
    for row in list_resources('strategy_feedback'):
        value = row['body']
        if (value.get('snapshot_id'), value.get('event'), value.get('publication_id', '')) == (identifier, event, publication_id):
            return row
    return record_strategy_feedback(identifier, {'event': event, 'publication_id': publication_id}, source='app_event')
