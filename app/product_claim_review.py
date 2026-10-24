"""Typed product propositions, exact public surfaces and bounded evidence levels.

Specification facts support values and normal functional affordances. They do
not become measured outcomes merely because a model labels them as such.
"""
from __future__ import annotations

from copy import deepcopy
import hashlib
import json
from typing import Literal
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from .marketing_surfaces import marketing_public_surfaces
from .models import AuditItem


class Strict(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)


class EvidenceQuote(Strict):
    evidence_id: str = Field(min_length=1, max_length=160)
    quote: str = Field(max_length=3000)


class ProductClaim(Strict):
    surface: str = Field(min_length=1, max_length=160)
    quote: str = Field(max_length=16000)
    proposition: str = Field(min_length=1, max_length=1000)
    claim_type: Literal['spec', 'affordance', 'performance', 'experience', 'invitation', 'expression', 'asset_identity', 'asset_appearance']
    evidence_ids: list[str] = Field(max_length=8)
    evidence_quotes: list[EvidenceQuote] = Field(max_length=8)
    support_level: Literal['exact_spec', 'functional_entailment', 'explicit_outcome', 'actual_record', 'none', 'authorized_asset', 'observed_pixels']
    verdict: Literal['supported', 'unsupported', 'not_product_claim']
    reason: str = Field(min_length=1, max_length=1600)


class ProductClaimReview(Strict):
    claims: list[ProductClaim] = Field(max_length=64)
    coverage: dict[str, Literal['claims_listed', 'no_product_claim']]


PRODUCT_CLAIM_SCHEMA = ProductClaimReview.model_json_schema()
PRODUCT_CLAIM_INSTRUCTION = '''本轮另必须返回product_claim_review，严格采用本次schema的surfaces分组。每个公开字段ID对应独立命题数组；必须覆盖所有给定字段，确实无产品或经历命题时给空数组。
每个命题只填判断、类型与实际证据ID，不返回surface、quote或coverage，服务器按所在分组绑定真实原文。
一句兼有亲历叙事与正常操作时必须拆开，例如“我拍了两张”是experience，“切长焦取远景”是affordance；不得用正常操作许可掩盖未经提供的已发生经历。标题和图卡也必须逐项考虑。
claim_type: spec=精确规格值/型号；affordance=已支持功能的正常操作可供性；performance=使用时长/画质/可靠性/性能或结果承诺；
experience=真实已发生的亲历/顾客记录；invitation=让顾客自主尝试评价的邀请；expression=非产品事实的情境/隐喻/审美。
asset_identity=实际选中素材的目录型号说明，需authorized_asset和asset_evidence中同类记录；不等于像素SKU认证。asset_appearance=该素材已观察的可见外观，需observed_pixels和同类SHA绑定记录；只按allowed_surfaces使用。型号说明不强求规格Fact，已观察外观不强求参数页原句；但目录型号不能证明可见颜色，图像不能证明功能、性能、实拍效果或亲历。
spec需exact_spec和对应事实证据；affordance可functional_entailment：明确有长焦则可原地切焦段选近远景，不需官网逐字写场所/动作。
performance supported必须引用outcome_evidence中的独立结果证据，不能用普通fact规格冒充outcome；容量不证明全天续航，像素不证明更清晰。
experience supported必须引用actual_records中的真实记录，不能编造亲测或常听顾客说。两类独立证据为空则相应断言不能supported。
邀请自己翻原图判断好不好看属于invitation，不是卖家保证画质；普通拍照/取景讨论不要推导成效果承诺。
同时句中有明确保证效果须拆成performance，不能用邀请掩盖。正确主摄/长焦简称不要求全抄营销命名。
evidence_ids只填本次schema枚举的事实或独立证据ID；evidence_quotes只填evidence_id，由服务器回填捕获原文，不输出quote。对unsupported可无支持证据并明确缺失。
不能因六维、本地场景或内部编辑草案没写全而给产品unsupported。不同呈现面的必要条件不当一次发布重复。
不要返回或重写产品总分；程序依据逐条verdict派生产品核验结果。'''


def product_claim_surfaces(draft):
    return {key: value for key, value in marketing_public_surfaces(draft).items()
            if value.strip() and '.disclosure:' not in key}


def product_claim_wire_schema(draft, evidence_ids=None):
    """Give the model the exact coverage keys rather than an open dictionary."""
    schema = deepcopy(PRODUCT_CLAIM_SCHEMA)
    keys = list(product_claim_surfaces(draft))
    schema['properties']['coverage'] = {'type': 'object', 'additionalProperties': False,
        'properties': {key: {'type': 'string', 'enum': ['claims_listed', 'no_product_claim']} for key in keys},
        'required': keys}
    schema['$defs']['ProductClaim']['properties']['surface']['enum'] = keys
    schema['$defs']['ProductClaim']['properties']['quote'] = {'type': 'string', 'const': ''}
    schema['$defs']['EvidenceQuote']['properties']['quote'] = {'type': 'string', 'const': ''}
    if evidence_ids is not None:
        identifiers = list(dict.fromkeys(evidence_ids))
        schema['$defs']['ProductClaim']['properties']['evidence_ids']['items']['enum'] = identifiers
        schema['$defs']['EvidenceQuote']['properties']['evidence_id']['enum'] = identifiers
    return schema


class ClaimReviewBoundaryError(ValueError):
    def __init__(self, code, diagnostics):
        self.diagnostics = diagnostics
        super().__init__(code)


def _plain(value):
    return value.model_dump(mode='json') if hasattr(value, 'model_dump') else value


def validate_product_claim_review(raw, draft, bundle, *, outcome_evidence=(), actual_records=(), asset_evidence=()):
    parsed = ProductClaimReview.model_validate(raw)
    surfaces = product_claim_surfaces(draft)
    if set(parsed.coverage) != set(surfaces):
        raise ClaimReviewBoundaryError('claim_surface_coverage_mismatch', {
            'missing_expected_surfaces': sorted(set(surfaces) - set(parsed.coverage)),
            'unexpected_surface_count': len(set(parsed.coverage) - set(surfaces))})
    bundle = _plain(bundle)
    facts = {row['id']: row for row in bundle.get('facts', [])}
    outcomes = {row['id']: row for row in outcome_evidence}
    records = {row['id']: row for row in actual_records}
    asset_identity = {row['id']: row for row in asset_evidence if row.get('kind') == 'asset_identity'}
    asset_appearance = {row['id']: row for row in asset_evidence if row.get('kind') == 'asset_appearance'}
    groups = (facts, outcomes, records, asset_identity, asset_appearance)
    if sum(len(group) for group in groups) != len(set().union(*groups)):
        raise ValueError('product_claim_evidence_id_collision')
    inventory = {**facts, **outcomes, **records, **asset_identity, **asset_appearance}
    seen_surfaces = set()
    claims = []
    for claim in parsed.claims:
        if claim.surface not in surfaces:
            raise ValueError('unknown_product_claim_surface')
        actual = surfaces[claim.surface]
        if claim.quote and (not claim.quote.strip() or claim.quote not in actual):
            raise ValueError('product_claim_quote_mismatch')
        if not claim.quote:
            claim.quote = actual
        if set(claim.evidence_ids) - inventory.keys():
            raise ValueError('unknown_product_claim_evidence')
        for span in claim.evidence_quotes:
            if span.evidence_id not in claim.evidence_ids:
                raise ValueError('unbound_product_evidence_quote')
            evidence = inventory[span.evidence_id]
            if span.quote == '':
                # Exact evidence IDs identify the immutable captured record;
                # do not ask a model to transcribe its text back to us.
                span.quote = str(evidence.get('value') or evidence.get('quote') or evidence.get('text') or '')
            text = '\n'.join(str(evidence.get(key) or '') for key in ('quote', 'value', 'text'))
            if not span.quote.strip() or span.quote not in text:
                raise ValueError('product_evidence_quote_mismatch')
        original_verdict = claim.verdict
        if claim.verdict == 'supported':
            required = {'spec': 'exact_spec', 'affordance': 'functional_entailment',
                        'performance': 'explicit_outcome', 'experience': 'actual_record',
                        'asset_identity': 'authorized_asset', 'asset_appearance': 'observed_pixels'}.get(claim.claim_type)
            if (claim.claim_type in {'invitation', 'expression'} and claim.support_level == 'none'
                    and not claim.evidence_ids and not claim.evidence_quotes):
                # The typed judgement says this is not a product proposition;
                # its redundant aggregate word does not turn it into a claim.
                claim.verdict = 'not_product_claim'
            elif required is None or claim.support_level != required or not claim.evidence_ids or not claim.evidence_quotes:
                # Valid locations/IDs with inadequate support are a rejected
                # proposition for the existing repair loop, not an unreadable
                # protocol. Never manufacture a stronger evidence level.
                claim.verdict = 'unsupported'
                claim.reason = ('证据层级不足：' + claim.claim_type + '需要' + str(required)
                    + '及逐字支持材料；当前为' + claim.support_level + '。' + claim.reason)
            allowed = {'performance': outcomes, 'experience': records,
                       'asset_identity': asset_identity, 'asset_appearance': asset_appearance}.get(claim.claim_type, facts)
            if not set(claim.evidence_ids).issubset(allowed):
                # Do not let a capacity/spec ID be relabelled as an outcome.
                claim.verdict = 'unsupported'
                claim.reason = '当前引用仅是规格/能力材料，未提供该类使用结果或亲历证据。' + claim.reason
            if claim.claim_type in {'asset_identity', 'asset_appearance'} and any(
                    claim.surface not in allowed[identifier].get('allowed_surfaces', [])
                    for identifier in claim.evidence_ids if identifier in allowed):
                claim.verdict = 'unsupported'
                claim.reason = '素材证据未绑定当前公开字段，不能挪用于另一幅图或页面。' + claim.reason
        elif claim.verdict == 'not_product_claim':
            if claim.claim_type not in {'invitation', 'expression'} or claim.support_level != 'none':
                claim.verdict = 'unsupported'
                claim.reason = '该类型不能作为非产品主张豁免；需按实际命题提供对应层级证据。' + claim.reason
        seen_surfaces.add(claim.surface)
        record = claim.model_dump(mode='json')
        if claim.verdict != original_verdict:
            record['support_diagnostics'] = {'surface': claim.surface, 'claim_type': claim.claim_type,
                'support_level': claim.support_level, 'original_verdict': original_verdict,
                'resolved_verdict': claim.verdict, 'evidence_count': len(claim.evidence_ids),
                'evidence_quote_count': len(claim.evidence_quotes), 'all_evidence_ids_known': True}
        record['claim_id'] = 'pc_' + hashlib.sha256(json.dumps({key: record[key] for key in
            ('surface', 'quote', 'proposition', 'claim_type')}, ensure_ascii=False, sort_keys=True).encode()).hexdigest()[:20]
        claims.append(record)
    for key, status in parsed.coverage.items():
        if (status == 'claims_listed') != (key in seen_surfaces):
            raise ValueError('product_claim_coverage_contradiction')
    errors = [row for row in claims if row['verdict'] == 'unsupported']
    return {'claims': claims, 'coverage': parsed.coverage,
            'verdict': 'unsupported' if errors else 'supported' if any(x['verdict'] == 'supported' for x in claims) else 'no_product_claims'}


def product_claim_audit(review):
    errors = [row for row in review['claims'] if row['verdict'] == 'unsupported']
    if errors:
        return [AuditItem(severity='error', category='semantic',
                          message=f"{row['surface']}『{row['quote']}』：{row['proposition']}；{row['reason']}") for row in errors]
    return [AuditItem(severity='info', category='semantic', message='逐条产品主张均有直接支持，正常功能可供性与效果承诺已分开核验，核验通过')]


def reconcile_marketing_product(review, claims):
    """Reuse a typed verdict; retain genuinely new located product findings."""
    value = deepcopy(review)
    row = value['dimensions']['product']
    findings = []
    for finding in row.get('findings', []):
        reference = finding.get('related_product_claim_id', '')
        claim = next((c for c in claims['claims'] if c['claim_id'] == reference), None) if reference else None
        if reference and (claim is None or claim['surface'] != finding['region']):
            raise ValueError('unknown_or_cross_surface_product_claim_reference')
        covered = claim is not None
        if not covered:
            findings.append(finding)
    errors = [c for c in claims['claims'] if c['verdict'] == 'unsupported']
    if errors:
        row.update(status='revise', rationale='逐条产品命题存在未获支持的具体断言。', findings=[
            {'quote': c['quote'], 'region': c['surface'], 'reason': c['reason'], 'action': '仅改该未支持命题，保留准确规格、正常操作与创意。'}
            for c in errors[:8]])
        if not str(value.get('revision_brief') or '').strip():
            # A validated typed rejection must reach repair, even when the
            # separate marketing response redundantly passed without a brief.
            value['revision_brief'] = '；'.join(f"{c['surface']}：{c['reason']}" for c in errors[:4])[:3000]
    elif findings:
        row['findings'] = findings  # New located omissions still block; never silently drop them.
    else:
        row.update(status='pass', rationale='消费已验证的逐条产品命题结果；不把正常操作或自主评价重新判为性能保证。', findings=[])
    from .marketing_review import MarketingReview
    return MarketingReview.model_validate(value).model_dump(mode='json')
