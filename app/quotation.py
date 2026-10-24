"""Approved, versioned price contracts with bounded exact discount selection.

This module performs no payment and never accepts a model/customer assertion
as price authority or qualification approval. Every quote binds the actual
rule and qualification resource versions used in its calculation.
"""
from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
from decimal import Decimal, ROUND_HALF_EVEN, ROUND_HALF_UP, localcontext
import hashlib
import itertools
import json
import time
from typing import Annotated, Literal
from uuid import uuid4

from fastapi import HTTPException
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from .tenancy import (authorize_resource, authorize_scope, list_resources, require_context,
    save_body, service_update_body, transaction_context)
from .tenancy.context import MANAGERS

RULE = 'quotation_rule'
QUALIFICATION = 'quotation_qualification'
QUOTE = 'quotation'
MAX_MINOR = 100_000_000_000
MAX_PROMOTIONS = 12
Money = Annotated[int, Field(strict=True, ge=0, le=MAX_MINOR)]
PositiveMoney = Annotated[int, Field(strict=True, ge=1, le=MAX_MINOR)]
Quantity = Annotated[int, Field(strict=True, ge=1, le=1000)]


class Contract(BaseModel):
    model_config = ConfigDict(extra='forbid', str_strip_whitespace=True)


class Component(Contract):
    sku: str = Field(min_length=1, max_length=120)
    name: str = Field(min_length=1, max_length=200)
    quantity: Quantity
    unit_minor: Money


class Promotion(Contract):
    id: str = Field(min_length=1, max_length=60, pattern=r'^[A-Za-z0-9_-]+$')
    label: str = Field(min_length=1, max_length=160)
    kind: Literal['fixed', 'rate']
    scope: Literal['unit', 'quote']
    amount_minor: Money | None = None
    basis_points: Annotated[int, Field(strict=True, ge=0, le=10000)] | None = None
    basis: Literal['base', 'remaining'] = 'base'
    rounding: Literal['half_up', 'half_even'] = 'half_up'
    cap_minor: Money | None = None
    cap_scope: Literal['unit', 'quote'] = 'quote'
    minimum_base_minor: Money = 0
    mode: Literal['exclusive', 'combinable'] = 'exclusive'
    exclusive_group: str = Field(default='', max_length=80)
    combinable_with: list[str] = Field(default_factory=list, max_length=MAX_PROMOTIONS)
    required_qualifications: list[str] = Field(default_factory=list, max_length=8)
    priority: Annotated[int, Field(strict=True, ge=0, le=1000)] = 100

    @model_validator(mode='after')
    def numeric_contract(self):
        if self.kind == 'fixed' and (self.amount_minor is None or self.basis_points is not None):
            raise ValueError('固定优惠必须提供整数分金额，不提供比例')
        if self.kind == 'rate' and (self.basis_points is None or self.amount_minor is not None):
            raise ValueError('比例优惠必须提供万分比，不提供固定金额')
        if self.id in self.combinable_with or len(self.combinable_with) != len(set(self.combinable_with)):
            raise ValueError('可叠加列表不能包含自身或重复编号')
        return self


def _instant(value):
    try:
        moment = datetime.fromisoformat(value.replace('Z', '+00:00'))
    except (TypeError, ValueError):
        raise ValueError('有效期请使用带时区的ISO时间') from None
    if moment.tzinfo is None:
        raise ValueError('有效期需要明确时区')
    return moment.timestamp()


class RuleInput(Contract):
    store_id: str = Field(min_length=1, max_length=120)
    product_name: str = Field(min_length=1, max_length=200)
    sku: str = Field(min_length=1, max_length=120)
    configuration: str = Field(min_length=1, max_length=300)
    currency: Literal['CNY'] = 'CNY'
    tax_included: bool = Field(strict=True)
    unit_minor: PositiveMoney
    fees_minor: Money = 0
    min_quantity: Quantity = 1
    max_quantity: Quantity = 1
    valid_from: str = Field(min_length=1, max_length=60)
    valid_until: str = Field(min_length=1, max_length=60)
    source_id: str = Field(min_length=1, max_length=200)
    source_excerpt: str = Field(min_length=5, max_length=2000)
    required_qualifications: list[str] = Field(default_factory=list, max_length=8)
    components: list[Component] = Field(default_factory=list, max_length=20)
    promotions: list[Promotion] = Field(default_factory=list, max_length=MAX_PROMOTIONS)
    is_demo: bool = Field(default=False, strict=True)

    @model_validator(mode='after')
    def validate_contract(self):
        if not self.tax_included:
            raise ValueError('请先核准含税金额口径')
        if _instant(self.valid_until) <= _instant(self.valid_from):
            raise ValueError('结束时间应晚于开始时间')
        if self.max_quantity < self.min_quantity:
            raise ValueError('授权数量范围无效')
        if self.components and sum(part.quantity*part.unit_minor for part in self.components) != self.unit_minor:
            raise ValueError('套装组件合计与套装单价不一致')
        identifiers = {promo.id for promo in self.promotions}
        if len(identifiers) != len(self.promotions):
            raise ValueError('优惠编号不能重复')
        if any(set(promo.combinable_with)-identifiers for promo in self.promotions):
            raise ValueError('可叠加列表只能引用本规则的优惠编号')
        return self


class QualificationInput(Contract):
    store_id: str = Field(min_length=1, max_length=120)
    subject_id: str = Field(min_length=1, max_length=160)
    code: str = Field(min_length=1, max_length=100, pattern=r'^[A-Za-z0-9_-]+$')
    source_id: str = Field(min_length=1, max_length=200)
    source_excerpt: str = Field(min_length=5, max_length=1500)
    valid_from: str = Field(min_length=1, max_length=60)
    valid_until: str = Field(min_length=1, max_length=60)

    @model_validator(mode='after')
    def interval(self):
        if _instant(self.valid_until) <= _instant(self.valid_from):
            raise ValueError('资格有效期无效')
        return self


class QuoteInput(Contract):
    rule_id: str = Field(min_length=1, max_length=160)
    quantity: Quantity = 1
    subject_id: str = Field(default='', max_length=160)


def _live():
    context = require_context()
    if context.is_legacy:
        raise HTTPException(403, '报价需要企业工作空间')
    if context.session_hash:
        from .resource_access import ensure_live_context
        context = ensure_live_context()
    return context


def _role(store_id, action):
    context = _live()
    authorize_scope(store_id, action)
    return context


def _view(row):
    return {**deepcopy(row['body']), 'resource_version': row['version']}


def _source_hash(data):
    return hashlib.sha256(json.dumps({key: data[key] for key in ('source_id', 'source_excerpt')},
        ensure_ascii=False, sort_keys=True).encode()).hexdigest()


def create_rule(payload):
    value = payload if isinstance(payload, RuleInput) else RuleInput.model_validate(payload)
    context = _role(value.store_id, 'manage')
    body = {**value.model_dump(), 'id': uuid4().hex, 'version': 1, 'status': 'draft',
        'created_by': context.user_id, 'authors': [context.user_id], 'created_at': time.time(),
        'source_hash': _source_hash(value.model_dump()), 'approval': None}
    return _view(save_body(RULE, body['id'], body, store_id=value.store_id, visibility='store'))


def update_rule(identifier, payload, *, expected_version):
    row = authorize_resource(RULE, identifier)
    old = row['body']
    context = _role(row['store_id'], 'manage')
    value = payload if isinstance(payload, RuleInput) else RuleInput.model_validate(payload)
    if value.store_id != row['store_id']:
        raise HTTPException(422, '报价规则不能移动到其他门店')
    body = {**value.model_dump(), 'id': identifier, 'version': old['version']+1,
        'status': 'draft', 'created_by': old['created_by'], 'created_at': old['created_at'],
        'authors': list(dict.fromkeys([*old.get('authors', [old['created_by']]), context.user_id])),
        'source_hash': _source_hash(value.model_dump()), 'approval': None, 'updated_at': time.time()}
    return _view(service_update_body(RULE, identifier, body, expected_version=expected_version))


def approve_rule(identifier, *, expected_version, note=''):
    row = authorize_resource(RULE, identifier)
    body = row['body']
    context = _role(row['store_id'], 'review')
    if context.user_id in body.get('authors', [body['created_by']]):
        raise HTTPException(403, '请由另一位审核者批准价格规则')
    if body['status'] != 'draft':
        raise HTTPException(409, '当前价格规则不处于待审核状态')
    RuleInput.model_validate({key: body[key] for key in RuleInput.model_fields})
    return _view(service_update_body(RULE, identifier, {**body, 'status': 'approved',
        'approval': {'by': context.user_id, 'at': time.time(), 'note': str(note)[:1000]}}, expected_version=expected_version))


def revoke_rule(identifier, *, expected_version, note=''):
    row = authorize_resource(RULE, identifier)
    _role(row['store_id'], 'manage')
    return _view(service_update_body(RULE, identifier, {**row['body'], 'status': 'revoked',
        'revocation': {'by': require_context().user_id, 'at': time.time(), 'note': str(note)[:1000]}}, expected_version=expected_version))


def list_rules(*, store_id='', sku=''):
    context = _live()
    target = store_id or context.store_id
    if target:
        authorize_scope(target)
    result = []
    for row in list_resources(RULE):
        body = row['body']
        if target and row['store_id'] != target or sku and body.get('sku') != sku:
            continue
        if body['status'] != 'approved' and not set(context.scoped_roles(row['store_id'])).intersection(MANAGERS | {'reviewer'}):
            continue
        result.append(_view(row))
    return result


def create_qualification(payload):
    value = payload if isinstance(payload, QualificationInput) else QualificationInput.model_validate(payload)
    context = _role(value.store_id, 'read')
    body = {**value.model_dump(), 'id': uuid4().hex, 'version': 1, 'status': 'draft',
        'created_by': context.user_id, 'created_at': time.time(),
        'source_hash': _source_hash(value.model_dump()), 'approval': None}
    return _view(save_body(QUALIFICATION, body['id'], body, store_id=value.store_id, visibility='store'))


def approve_qualification(identifier, *, expected_version, note=''):
    row = authorize_resource(QUALIFICATION, identifier)
    body = row['body']
    context = _role(row['store_id'], 'review')
    if context.user_id == body['created_by']:
        raise HTTPException(403, '请由另一位审核者核验资格依据')
    if body['status'] != 'draft':
        raise HTTPException(409, '当前资格不处于待审核状态')
    return _view(service_update_body(QUALIFICATION, identifier, {**body, 'status': 'approved',
        'approval': {'by': context.user_id, 'at': time.time(), 'note': str(note)[:1000]}}, expected_version=expected_version))


def revoke_qualification(identifier, *, expected_version, note=''):
    row = authorize_resource(QUALIFICATION, identifier)
    _role(row['store_id'], 'review')
    return _view(service_update_body(QUALIFICATION, identifier, {**row['body'], 'status': 'revoked',
        'revocation': {'by': require_context().user_id, 'at': time.time(), 'note': str(note)[:1000]}}, expected_version=expected_version))


def _qualification_rows(store_id, subject_id):
    now = time.time()
    result = {}
    if not subject_id:
        return result
    for row in list_resources(QUALIFICATION):
        body = row['body']
        if (row['store_id'] == store_id and body['subject_id'] == subject_id
                and body['status'] == 'approved' and body.get('approval')
                and body['approval']['by'] != body['created_by']
                and _instant(body['valid_from']) <= now < _instant(body['valid_until'])):
            previous = result.get(body['code'])
            if previous is None or (_instant(body['valid_until']), row['id']) > (_instant(previous['body']['valid_until']), previous['id']):
                result[body['code']] = row
    return result


def _compatible(promos):
    if len(promos) < 2:
        return True
    for a, b in itertools.combinations(promos, 2):
        if a.mode == 'exclusive' or b.mode == 'exclusive':
            return False
        if a.exclusive_group and a.exclusive_group == b.exclusive_group:
            return False
        if b.id not in a.combinable_with or a.id not in b.combinable_with:
            return False
    return True


def _calculate_subset(rule, quantity, subset):
    base = rule.unit_minor*quantity
    remaining, trace = base, []
    for promo in sorted(subset, key=lambda item: (item.priority, item.id)):
        before = remaining
        if promo.kind == 'fixed':
            amount = promo.amount_minor*(quantity if promo.scope == 'unit' else 1)
        else:
            basis = base if promo.basis == 'base' else remaining
            with localcontext() as decimal_context:
                decimal_context.prec = 40
                raw = Decimal(basis) / (quantity if promo.scope == 'unit' else 1)
                raw = raw * Decimal(promo.basis_points) / Decimal(10000)
                rounding = ROUND_HALF_UP if promo.rounding == 'half_up' else ROUND_HALF_EVEN
                amount = int(raw.quantize(Decimal('1'), rounding=rounding))
                if promo.scope == 'unit':
                    amount *= quantity
        if promo.cap_minor is not None:
            amount = min(amount, promo.cap_minor*(quantity if promo.cap_scope == 'unit' else 1))
        if amount > remaining:
            return None
        remaining -= amount
        trace.append({'promotion_id': promo.id, 'label': promo.label, 'before_minor': before,
            'deduction_minor': amount, 'after_minor': remaining, 'scope': promo.scope,
            'basis': promo.basis, 'rounding': promo.rounding})
    total = remaining + rule.fees_minor
    if total > MAX_MINOR:
        return None
    return {'base_minor': base, 'fees_minor': rule.fees_minor, 'discount_minor': base-remaining,
        'total_minor': total, 'trace': trace, 'promotion_ids': [item['promotion_id'] for item in trace]}


def optimize(rule, quantity, qualification_codes=()):
    """Exact best valid subset under the approved ordering, at most 2^12 sets."""
    rule = rule if isinstance(rule, RuleInput) else RuleInput.model_validate(rule)
    if type(quantity) is not int or not rule.min_quantity <= quantity <= rule.max_quantity:
        raise HTTPException(422, '数量不在本规则授权范围内')
    if rule.unit_minor*quantity + rule.fees_minor > MAX_MINOR:
        raise HTTPException(422, '报价金额超过计算上限')
    eligible, rejected = [], []
    available = set(qualification_codes)
    for promo in rule.promotions:
        missing = sorted(set(promo.required_qualifications)-available)
        if missing:
            rejected.append({'promotion_id': promo.id, 'reason': 'qualification_required', 'missing': missing})
        elif rule.unit_minor*quantity < promo.minimum_base_minor:
            rejected.append({'promotion_id': promo.id, 'reason': 'minimum_purchase_not_met'})
        else:
            eligible.append(promo)
    best, feasible, rejected_sets = None, 0, 0
    for mask in range(1 << len(eligible)):
        subset = [promo for index, promo in enumerate(eligible) if mask & (1 << index)]
        if not _compatible(subset):
            rejected_sets += 1
            continue
        result = _calculate_subset(rule, quantity, subset)
        if result is None:
            rejected_sets += 1
            continue
        feasible += 1
        key = (result['total_minor'], len(result['promotion_ids']), tuple(result['promotion_ids']))
        if best is None or key < best[0]:
            best = (key, result)
    if best is None:
        raise HTTPException(422, '授权优惠没有可计算的组合')
    result = best[1]
    selected = [promo for promo in eligible if promo.id in result['promotion_ids']]
    for promo in eligible:
        if promo.id not in result['promotion_ids']:
            conflicts = []
            for other in selected:
                if promo.mode == 'exclusive' or other.mode == 'exclusive':
                    conflicts.append({'with': other.id, 'reason': 'exclusive'})
                elif promo.exclusive_group and promo.exclusive_group == other.exclusive_group:
                    conflicts.append({'with': other.id, 'reason': 'same_exclusive_group'})
                elif other.id not in promo.combinable_with or promo.id not in other.combinable_with:
                    conflicts.append({'with': other.id, 'reason': 'combination_not_authorized_both_ways'})
            rejected.append({'promotion_id': promo.id, 'reason': 'not_in_best_valid_combination',
                'conflicts': conflicts})
    return {**result, 'rejected_promotions': rejected, 'optimality': {
        'method': 'bounded_exact_enumeration', 'promotion_bound': MAX_PROMOTIONS,
        'evaluated_combinations': 1 << len(eligible), 'feasible_combinations': feasible,
        'rejected_combinations': rejected_sets,
        'scope': 'approved_rule_version_and_verified_qualifications'}}


def prepare_quote(rule_id, *, quantity=1, subject_id=''):
    request = QuoteInput(rule_id=rule_id, quantity=quantity, subject_id=subject_id)
    _live()
    with transaction_context():
        row = authorize_resource(RULE, request.rule_id)
        body = row['body']
        _role(row['store_id'], 'read')
        if body['status'] != 'approved' or not body.get('approval') or body['approval']['by'] in body.get('authors', [body['created_by']]):
            return {'status': 'needs_approved_rule', 'rule_id': rule_id, 'message': '请先核准这份门店价格规则。'}
        now = time.time()
        if not _instant(body['valid_from']) <= now < _instant(body['valid_until']):
            return {'status': 'needs_current_rule', 'rule_id': rule_id, 'message': '请更新有效的门店价格资料。'}
        rule = RuleInput.model_validate({key: body[key] for key in RuleInput.model_fields})
        proof = _qualification_rows(row['store_id'], request.subject_id)
        missing = sorted(set(rule.required_qualifications)-set(proof))
        if missing:
            return {'status': 'needs_qualification', 'rule_id': rule_id, 'required_qualifications': missing,
                'message': '这份报价需要先核验对应资格。'}
        result = optimize(rule, quantity, set(proof))
        needed = set(rule.required_qualifications)
        for promo in rule.promotions:
            if promo.id in result['promotion_ids']:
                needed.update(promo.required_qualifications)
        used = [proof[code] for code in sorted(needed)]
        expires = min([_instant(rule.valid_until), now+3600] + [_instant(item['body']['valid_until']) for item in used])
        quote = {**result, 'id': uuid4().hex, 'status': 'ready', 'store_id': row['store_id'],
            'rule_id': rule_id, 'rule_version': body['version'], 'rule_resource_version': row['version'],
            'product_name': rule.product_name, 'sku': rule.sku, 'configuration': rule.configuration,
            'quantity': quantity, 'subject_id': request.subject_id, 'currency': 'CNY', 'tax_included': True,
            'unit_minor': rule.unit_minor, 'components': [component.model_dump() for component in rule.components],
            'source_id': rule.source_id, 'source_hash': body['source_hash'], 'approval': deepcopy(body['approval']),
            'qualification_versions': [{'id': item['id'], 'version': item['version'], 'code': item['body']['code']} for item in used],
            'created_at': now, 'valid_until': datetime.fromtimestamp(expires, timezone.utc).isoformat(),
            'is_demo': rule.is_demo}
        save_body(QUOTE, quote['id'], quote, store_id=row['store_id'], visibility='store')
    return get_quote(quote['id'])


def get_quote(identifier):
    _live()
    row = authorize_resource(QUOTE, identifier)
    body = deepcopy(row['body'])
    invalid = []
    now = time.time()
    if now >= _instant(body['valid_until']):
        invalid.append('quote_expired')
    try:
        rule_row = authorize_resource(RULE, body['rule_id'])
        rule = rule_row['body']
        if rule_row['version'] != body['rule_resource_version'] or rule['version'] != body['rule_version']:
            invalid.append('rule_changed')
        if rule['status'] != 'approved' or not _instant(rule['valid_from']) <= now < _instant(rule['valid_until']):
            invalid.append('rule_not_current')
    except HTTPException as exc:
        if exc.status_code != 404:
            raise
        invalid.append('rule_no_longer_accessible')
    for proof in body.get('qualification_versions', []):
        try:
            current = authorize_resource(QUALIFICATION, proof['id'])
            value = current['body']
            if (current['version'] != proof['version'] or value['status'] != 'approved'
                    or value['subject_id'] != body['subject_id'] or current['store_id'] != row['store_id']
                    or not _instant(value['valid_from']) <= now < _instant(value['valid_until'])):
                invalid.append('qualification_changed')
        except HTTPException as exc:
            if exc.status_code != 404:
                raise
            invalid.append('qualification_no_longer_accessible')
    if invalid:
        # Preserve the stored audit record, but do not distribute old amounts as a live offer.
        body.update(status='invalidated', total_minor=None, discount_minor=None,
            base_minor=None, unit_minor=None, fees_minor=None, trace=[], components=[],
            invalid_reasons=sorted(set(invalid)), message='价格或资格资料已更新，请重新核价。')
    return body
