"""Source-bound, durable local marketing work prepared by the conversational agent.

Plans describe a proposed experience, never an existing store service. Published
copy remains a separate, audited artifact; a plan cannot bypass product review.
"""
import hashlib
import json
import time
from typing import Literal

from fastapi import HTTPException
from pydantic import BaseModel, ConfigDict, Field

from ..tenancy import authorize_resource, register_resource, require_context, service_update_body, transaction_context

KIND = 'marketing_opportunity'


class ExperienceStep(BaseModel):
    model_config = ConfigDict(extra='forbid')
    seconds: int = Field(ge=10, le=180)
    action: str = Field(min_length=1, max_length=500)
    customer_value: str = Field(min_length=1, max_length=300)


class OpportunityPlan(BaseModel):
    model_config = ConfigDict(extra='forbid')
    title: str = Field(min_length=1, max_length=120)
    product: str = Field(min_length=1, max_length=200)
    rationale: str = Field(min_length=1, max_length=800)
    audience_hypothesis: str = Field(min_length=1, max_length=300)
    customer_task: str = Field(min_length=1, max_length=1000)
    content_angle: str = Field(min_length=1, max_length=800)
    visual_direction: str = Field(min_length=1, max_length=1000)
    evidence_ids: list[str] = Field(default_factory=list, max_length=12)
    fact_ids: list[str] = Field(min_length=1, max_length=4)
    capture_steps: list[str] = Field(default_factory=list, max_length=4)
    experience_steps: list[ExperienceStep] = Field(min_length=1, max_length=5)
    materials: list[str] = Field(min_length=1, max_length=8)
    capabilities_to_confirm: list[str] = Field(min_length=1, max_length=6)
    next_action: str = Field(min_length=1, max_length=500)
    deliverables: list[Literal['content', 'images']] = Field(default_factory=lambda: ['content'], max_length=2)


def validate_plan(plan, *, place_context, product_evidence):
    value = plan if isinstance(plan, OpportunityPlan) else OpportunityPlan.model_validate(plan)
    bundle = product_evidence.get(value.product)
    if bundle is None:
        raise ValueError('先读取本次准确产品资料，再准备营销机会')
    if set(value.fact_ids) - {fact.id for fact in bundle.facts}:
        raise ValueError('体验方案只能引用已读取的产品事实ID')
    available = {item['source_id'] for item in place_context.get('sources', [])}
    if set(value.evidence_ids) - available:
        raise ValueError('营销机会只能引用本次门店研究实际返回的来源')
    if available and not value.evidence_ids:
        raise ValueError('本地营销机会需要引用支持判断的门店研究来源')
    if sum(step.seconds for step in value.experience_steps) > 180:
        raise ValueError('店员体验步骤总时长应控制在三分钟内')
    if place_context.get('state') == 'ambiguous':
        raise ValueError('先确认同名门店所在地点，再准备本地营销机会')
    return value


def get_opportunity(identifier, *, audience='private'):
    row = authorize_resource(KIND, identifier)
    context = require_context()
    if row['store_id'] != context.store_id or (row['visibility'] == 'private' and
            (row['owner_id'] != context.user_id or audience == 'group')):
        raise HTTPException(404, '没有找到这家门店的营销方案')
    return {**row['body'], 'version': row['version']}


def save_opportunity(plan, *, conversation, turn, place_context, product_evidence):
    from .repository import CONVERSATION, conversation_row
    plan = validate_plan(plan, place_context=place_context, product_evidence=product_evidence)
    identifier = hashlib.sha256((turn['id'] + json.dumps(plan.model_dump(), sort_keys=True,
        ensure_ascii=False)).encode()).hexdigest()[:32]
    bundle = product_evidence[plan.product]
    body = {**plan.model_dump(), 'id': identifier, 'status': 'prepared',
        'audience_basis': 'editorial_hypothesis', 'experience_status': 'proposed',
        'conversation_id': conversation['id'], 'turn_id': turn['id'],
        'created_at': time.time(), 'sources': [source for source in place_context.get('sources', [])
            if source['source_id'] in plan.evidence_ids],
        'product_facts': [fact.model_dump(exclude={'quote'}) for fact in bundle.facts if fact.id in plan.fact_ids],
        'artifacts': []}
    with transaction_context():
        row = register_resource(KIND, identifier, body=body,
            visibility='store' if conversation['audience'] == 'group' else 'private')
        current = conversation_row(conversation['id'], editing=True)
        service_update_body(CONVERSATION, current['id'], {**current['body'],
            'current_opportunity': row['body']}, expected_version=current['version'])
    return row['body']


def attach_artifact(plan_id, reference, *, audience='private'):
    body = get_opportunity(plan_id, audience=audience)
    artifacts = [item for item in body.get('artifacts', []) if item['id'] != reference['id']]
    artifacts.append(reference)
    row = authorize_resource(KIND, plan_id)
    updated = {**row['body'], 'artifacts': artifacts[-12:], 'status': 'content_ready'}
    service_update_body(KIND, plan_id, updated, expected_version=row['version'])
    return updated


def plan_requirements(plan):
    return '\n本次已准备的本地营销机会（作为创作策略，不是已存在的活动或服务）：' + json.dumps({
        key: plan[key] for key in ('title', 'audience_hypothesis', 'customer_task', 'content_angle',
        'visual_direction', 'fact_ids', 'capture_steps', 'experience_steps', 'capabilities_to_confirm')
    }, ensure_ascii=False) + '\n文案围绕同一个顾客任务展开，标题提出的兴趣点必须由正文相关产品事实兑现；待确认的服务或物料不可写成门店承诺。'
