import asyncio
from copy import deepcopy

import pytest

from app.product_claim_review import (product_claim_surfaces, validate_product_claim_review,
    product_claim_audit, reconcile_marketing_product, product_claim_wire_schema)
from app.models import EvidenceBundle, RunResult, TaskInput
from app.llm import OpenAICompatibleLLM, ModelCallError


def facts():
    return {'facts': [{'id': 'camera', 'value': '支持潜望式长焦及主摄拍照。', 'quote': '支持潜望式长焦及主摄拍照。'},
                      {'id': 'battery', 'value': '5750mAh', 'quote': '电池5750mAh（典型值）。'}]}


def test_insufficient_performance_support_becomes_actionable_rejection_not_schema_failure():
    raw, draft, evidence = typed(text='一天下来电量还够用。', kind='performance', evidence_id='battery', level='exact_spec')
    result = validate_product_claim_review(raw, draft, evidence)
    assert result['verdict'] == 'unsupported'
    assert result['claims'][0]['support_level'] == 'exact_spec'
    assert product_claim_audit(result)[0].severity == 'error'


def test_stable_ids_fill_exact_public_text_and_evidence_without_model_transcription():
    raw, draft, evidence = typed()
    raw['claims'][0]['evidence_quotes'][0]['quote'] = ''
    result = validate_product_claim_review(raw, draft, evidence)
    claim = result['claims'][0]
    assert claim['quote'] == draft['blocks'][0]['text']
    assert claim['evidence_quotes'][0]['quote'] == evidence['facts'][0]['value']
    schema = product_claim_wire_schema(draft, ['camera'])
    assert schema['$defs']['ProductClaim']['properties']['quote']['const'] == ''
    assert schema['$defs']['EvidenceQuote']['properties']['quote']['const'] == ''


def test_wire_schema_enumerates_actual_fact_ids_and_surfaces_not_span_aliases():
    schema = product_claim_wire_schema({'title': '观察街角'}, ['camera', 'battery'])
    assert schema['$defs']['ProductClaim']['properties']['evidence_ids']['items']['enum'] == ['camera', 'battery']
    assert schema['$defs']['EvidenceQuote']['properties']['evidence_id']['enum'] == ['camera', 'battery']
    assert schema['$defs']['ProductClaim']['properties']['surface']['enum'] == ['title']


def typed(text='切长焦把对岸灯拉近拍。', kind='affordance', evidence_id='camera', level='functional_entailment'):
    draft = {'title': '观察街角', 'blocks': [{'text': text}]}
    evidence = facts()
    row = next(x for x in evidence['facts'] if x['id'] == evidence_id)
    result = {'coverage': {key: 'no_product_claim' for key in product_claim_surfaces(draft)}, 'claims': [{
        'surface': 'body:0', 'quote': '', 'proposition': text, 'claim_type': kind,
        'evidence_ids': [evidence_id], 'evidence_quotes': [{'evidence_id': evidence_id, 'quote': row['quote']}],
        'support_level': level, 'verdict': 'supported', 'reason': '由已提供功能支持普通操作。'}]}
    result['coverage']['body:0'] = 'claims_listed'
    return result, draft, evidence


def test_normal_functional_action_does_not_require_official_scenario_wording():
    raw, draft, evidence = typed()
    result = validate_product_claim_review(raw, draft, evidence)
    assert result['verdict'] == 'supported'
    assert result['claims'][0]['quote'] == draft['blocks'][0]['text']
    assert not any(x.severity == 'error' for x in product_claim_audit(result))


def test_accurate_main_camera_abbreviation_is_normal_affordance():
    raw, draft, evidence = typed('用主摄顺手一按。')
    assert validate_product_claim_review(raw, draft, evidence)['verdict'] == 'supported'


def test_capacity_spec_cannot_be_relabelled_as_independent_day_long_outcome():
    raw, draft, evidence = typed('一天下来电量还够用，不用惦记充电。', 'performance', 'battery', 'explicit_outcome')
    result = validate_product_claim_review(raw, draft, evidence)
    assert result['verdict'] == 'unsupported'
    assert any(x.severity == 'error' for x in product_claim_audit(result))


def test_genuine_typed_outcome_record_can_support_matching_performance():
    raw, draft, evidence = typed('一天下来电量还够用。', 'performance', 'battery', 'explicit_outcome')
    raw['claims'][0]['evidence_ids'] = ['test1']
    raw['claims'][0]['evidence_quotes'] = [{'evidence_id': 'test1', 'quote': '指定条件下全天测试电量仍充足。'}]
    result = validate_product_claim_review(raw, draft, evidence,
        outcome_evidence=[{'id': 'test1', 'quote': '指定条件下全天测试电量仍充足。'}])
    assert result['verdict'] == 'supported'


@pytest.mark.parametrize('mutation', ['quote', 'evidence', 'span', 'surface', 'coverage', 'extra', 'exclusion'])
def test_strict_public_claim_and_evidence_boundary(mutation):
    raw, draft, evidence = typed()
    row = raw['claims'][0]
    if mutation == 'quote': row['quote'] = '不存在的公开文案'
    elif mutation == 'evidence': row['evidence_ids'] = ['unknown']
    elif mutation == 'span': row['evidence_quotes'][0]['quote'] = '全天高清'
    elif mutation == 'surface': row['surface'] = 'body:9'
    elif mutation == 'coverage': del raw['coverage']['title']
    elif mutation == 'extra': row['predicted_conversion'] = 0.99
    elif mutation == 'exclusion': row.update(verdict='not_product_claim', support_level='none')
    if mutation == 'exclusion':
        result = validate_product_claim_review(raw, draft, evidence)
        assert result['verdict'] == 'unsupported'
        assert product_claim_audit(result)[0].severity == 'error'
        return
    with pytest.raises(ValueError):
        validate_product_claim_review(raw, draft, evidence)


def test_self_evaluation_invitation_is_not_a_quality_promise():
    raw, draft, evidence = typed('好不好看，当场翻原图，自己判断。')
    raw['claims'][0].update(claim_type='invitation', verdict='not_product_claim', support_level='none',
                            evidence_ids=[], evidence_quotes=[], reason='邀请读者自主评价，没有断言结果好。')
    assert validate_product_claim_review(raw, draft, evidence)['verdict'] == 'no_product_claims'


def test_product_dimension_reuses_verified_claim_but_preserves_new_omission():
    raw, draft, evidence = typed()
    claims = validate_product_claim_review(raw, draft, evidence)
    review = {'schema_version': '1', 'phase': 'draft', 'dimensions': {key: {
        'status': 'pass', 'rationale': '无问题', 'findings': []} for key in
        ('product', 'world', 'legal', 'empathy', 'community', 'timeliness')},
        'creative_effectiveness': {'status': 'pass', 'hook': '具体', 'local_value': '具体',
          'brand_connection': '自然', 'natural_action': '自然', 'distinctiveness': '具体', 'preserve': [], 'improvements': []},
        'revision_brief': '仅补全营销名称'}
    review['dimensions']['product'].update(status='revise', findings=[{'quote': draft['blocks'][0]['text'],
        'region': 'body:0', 'reason': '希望照抄营销全称', 'action': '扩写',
        'related_product_claim_id': claims['claims'][0]['claim_id']}])
    assert reconcile_marketing_product(review, claims)['decision'] == 'accept'
    review['dimensions']['product']['findings'][0].update(region='body:0', quote='同段另一新性能承诺', related_product_claim_id='')
    assert reconcile_marketing_product(review, claims)['decision'] == 'revise'


def test_marketing_task_requires_typed_output_and_persists_safe_failure():
    class Model(OpenAICompatibleLLM):
        async def _json(self, *args, **kwargs):
            return {'verdict': 'supported', 'audit': []}
    model = Model()
    model.marketing_task = TaskInput(product='测试机')
    result = RunResult(title='观察街角', blocks=[])
    with pytest.raises(ModelCallError, match='invalid_product_claim_review_schema'):
        asyncio.run(model.verify(result, EvidenceBundle(product='测试机')))
    assert result.strategy['product_claim_review_failure']['stage'] == 'semantic_claims'
    assert all(set(x) == {'loc', 'type'} for x in result.strategy['product_claim_review_failure']['schema_errors'])


def test_failed_optional_preflight_retains_diagnostic_and_still_writes_real_draft(monkeypatch):
    from app import creative_exploration, marketing_review
    model = OpenAICompatibleLLM(max_calls=12)
    model.marketing_context = {'publisher': {}}
    called = []

    async def explore(*args, **kwargs):
        return {'selected_concept': {'opening': '街角灯光', 'payoff': '看看玻璃倒影。', 'angle': '街角观察'}}

    async def bad_preflight(*args, **kwargs):
        raise ValueError('Marketing review quotes non-public or nonexistent text')

    async def writer(system, payload, temperature):
        called.append(payload)
        return {'title': '真正成稿', 'blocks': []}

    monkeypatch.setattr(creative_exploration, 'explore_creative_directions', explore)
    monkeypatch.setattr(marketing_review, 'review_marketing', bad_preflight)
    monkeypatch.setattr(model, '_json', writer)
    output = asyncio.run(model.generate(TaskInput(product='测试机', audience='朋友', scene='街角', platform='moments'),
        EvidenceBundle(product='测试机'), {}))
    assert output['title'] == '真正成稿' and len(called) == 1
    assert model.creative_execution['exploration']['preflight_review']['status'] == 'unavailable'
    assert 'decision' not in model.creative_execution['exploration']['preflight_review']
    assert model.marketing_review_failure['stage'] == 'preflight'


def test_wire_coverage_schema_lists_exact_surfaces_and_missing_diagnostic_is_safe():
    raw, draft, evidence = typed()
    schema = product_claim_wire_schema(draft)['properties']['coverage']
    assert set(schema['required']) == set(product_claim_surfaces(draft))
    assert schema['additionalProperties'] is False
    del raw['coverage']['body:0']
    raw['coverage']['sensitive-untrusted-key'] = 'no_product_claim'
    with pytest.raises(ValueError) as caught:
        validate_product_claim_review(raw, draft, evidence)
    assert caught.value.diagnostics['missing_expected_surfaces'] == ['body:0']
    assert caught.value.diagnostics['unexpected_surface_count'] == 1
    assert 'sensitive-untrusted-key' not in str(caught.value.diagnostics)
