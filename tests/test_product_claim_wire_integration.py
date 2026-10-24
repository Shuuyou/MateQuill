"""Exercise grouped model output through the real verify boundary, offline."""
import asyncio
from copy import deepcopy
import hashlib
import json

import pytest

from app.llm import ModelCallError, OpenAICompatibleLLM
from app.models import EvidenceBundle, Fact, RunResult, Source, TaskInput
from app.product_claim_review import product_claim_surfaces


def claim(proposition='可切长焦看远景'):
    return {'proposition': proposition, 'claim_type': 'affordance',
            'evidence_ids': ['camera'], 'evidence_quotes': [{'evidence_id': 'camera'}],
            'support_level': 'functional_entailment', 'verdict': 'supported',
            'reason': '由已提供的长焦能力支持正常取景。'}


@pytest.fixture
def verifier(monkeypatch):
    model = OpenAICompatibleLLM(api_key='offline-test-placeholder')
    # This fixture isolates the existing product-wire contract. Production
    # reader-value enforcement is covered by test_reader_value_workflow.py.
    model.supports_reader_value_contract = False
    model.marketing_task = TaskInput(product='测试机', platform='moments')
    result = RunResult(title='看看街角', intro='换个取景看看。',
        blocks=[{'text': '切长焦看远景，再切主摄看身边。', 'fact_ids': ['camera']}],
        cta='喜欢哪张留哪张。')
    bundle = EvidenceBundle(product='测试机', facts=[Fact(id='camera', product='测试机',
        label='后置摄像头', value='支持主摄与潜望式长焦拍照。',
        quote='支持主摄与潜望式长焦拍照。', source_id='official')],
        sources=[Source(id='official', title='捕获的官方原文', excerpt='支持主摄与潜望式长焦拍照。')])
    surfaces = product_claim_surfaces(model._draft(result))
    raw = {'surfaces': {key: [] for key in surfaces}}
    raw['surfaces']['body:0'] = [claim(), claim('可切主摄看身边')]
    captured = []

    async def fake_json(system, payload, *args, **kwargs):
        captured.append(deepcopy(payload))
        if 'product_claim_schema' in payload:
            # A false redundant aggregate cannot override the typed claims.
            return {'verdict': 'no_product_claims', 'audit': [],
                    'product_claim_review': deepcopy(raw)}
        return {'schema_version': '1', 'phase': 'draft',
            'dimensions': {key: {'status': 'pass', 'rationale': '无其他问题', 'findings': []}
                for key in ('product', 'world', 'legal', 'empathy', 'community', 'timeliness')},
            'creative_effectiveness': {'status': 'pass', 'hook': '具体动作', 'local_value': '观察街景',
                'brand_connection': '自然取景', 'natural_action': '自主比较',
                'distinctiveness': '两种构图', 'preserve': [], 'improvements': []},
            'revision_brief': ''}

    monkeypatch.setattr(model, '_json', fake_json)
    return model, result, bundle, raw, surfaces, captured


def test_verify_decodes_real_grouped_response_and_passes_typed_review_to_marketing(verifier):
    model, result, bundle, raw, surfaces, captured = verifier
    original = deepcopy(raw)
    audit = asyncio.run(model.verify(result, bundle))
    assert raw == original
    schema = captured[0]['product_claim_schema']
    assert set(schema['properties']) == {'surfaces'}
    assert schema['properties']['surfaces']['required'] == list(surfaces)
    assert schema['properties']['surfaces']['additionalProperties'] is False
    assert captured[0]['product_claim_surfaces'] == surfaces
    assert {'title', 'intro', 'body:0', 'cta'} <= set(surfaces)
    assert schema['$defs']['ProductClaim']['properties']['evidence_ids']['items']['enum'] == ['camera']
    assert schema['$defs']['EvidenceQuote']['properties']['evidence_id']['enum'] == ['camera']
    typed = result.strategy['product_claim_review']
    assert model.product_claim_review == typed
    assert typed['verdict'] == 'supported'
    assert typed['coverage'] == {key: 'claims_listed' if key == 'body:0' else 'no_product_claim'
                                 for key in surfaces}
    assert [row['proposition'] for row in typed['claims']] == ['可切长焦看远景', '可切主摄看身边']
    for row in typed['claims']:
        assert row['surface'] == 'body:0' and row['quote'] == surfaces['body:0']
        assert row['evidence_quotes'] == [{'evidence_id': 'camera', 'quote': bundle.facts[0].value}]
        assert row['claim_id'].startswith('pc_')
    assert captured[1]['context']['validated_product_claim_review'] == typed
    assert model.marketing_review['decision'] == 'accept'
    assert not any(item.severity == 'error' for item in audit)


@pytest.mark.parametrize('mutation,code', [
    ('missing_group', 'product_claim_wire_surface_mismatch'),
    ('extra_group', 'product_claim_wire_surface_mismatch'),
    ('span_alias_id', 'product_claim_wire_unknown_evidence'),
    ('unknown_span_id', 'product_claim_wire_unknown_evidence'),
    ('surface_override', 'product_claim_wire_claim_invalid'),
])
def test_verify_fails_closed_on_grouped_boundary_and_persists_only_safe_diagnostic(verifier, mutation, code):
    model, result, bundle, raw, surfaces, captured = verifier
    row = raw['surfaces']['body:0'][0]
    if mutation == 'missing_group':
        del raw['surfaces']['title']
    elif mutation == 'extra_group':
        raw['surfaces']['private-extra-surface'] = []
    elif mutation == 'span_alias_id':
        row['evidence_ids'] = ['E1']
    elif mutation == 'unknown_span_id':
        row['evidence_quotes'][0]['evidence_id'] = 'private-unknown-evidence'
    else:
        row['surface'] = 'title'
    with pytest.raises(ModelCallError, match='^invalid_product_claim_review_schema$'):
        asyncio.run(model.verify(result, bundle))
    diagnostic = result.strategy['product_claim_review_failure']
    assert diagnostic == {'code': 'invalid_product_claim_review_schema', 'stage': 'semantic_claims',
                          'schema_errors': [], 'boundary_code': code}
    assert model.product_claim_review_failure == diagnostic
    assert 'product_claim_review' not in result.strategy
    assert len(captured) == 1  # Invalid claims never reach the marketing reviewer.
    assert 'private-' not in repr(diagnostic)


@pytest.mark.parametrize('misuse_as_performance', [False, True])
def test_verify_connects_selected_asset_identity_and_actual_pixel_observation(
        verifier, tmp_path, monkeypatch, misuse_as_performance):
    from PIL import Image
    from app import product_claim_review, visual_assets
    from app.asset_claim_evidence import build_asset_claim_evidence
    from app.models import Asset, ImagePlacement

    model, result, bundle, raw, _, captured = verifier
    path = tmp_path / 'authorized.png'
    Image.new('RGB', (8, 8), '#47a384').save(path)
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    registry = tmp_path / 'registered-observation.json'
    registry.write_text(json.dumps({digest: {'observed_title': '绿色方形图像', 'semantic_tags': ['绿色'],
        'visual_role': 'product_overview', 'usable': True}}), encoding='utf-8')
    monkeypatch.setattr(visual_assets, 'REVIEW_PATH', registry)
    bundle.assets = [Asset(id='selected', product='测试机', source_id='official',
        url='https://unused.invalid/no-fetch', verified=True, sha256=digest, local_path=str(path))]
    result.images = [ImagePlacement(asset_id='selected', caption='测试机产品资料图，绿色外观', position='封面')]
    surfaces = product_claim_surfaces(model._draft(result))
    evidence = build_asset_claim_evidence(model._draft(result), bundle)
    assert [row['kind'] for row in evidence] == ['asset_identity', 'asset_appearance']
    raw['surfaces'] = {surface: [] for surface in surfaces}
    for asset in evidence:
        row = claim(asset['text'])
        row.update(claim_type=asset['kind'], support_level=(
            'authorized_asset' if asset['kind'] == 'asset_identity' else 'observed_pixels'),
            evidence_ids=[asset['id']], evidence_quotes=[{'evidence_id': asset['id']}])
        if misuse_as_performance:
            row.update(claim_type='performance', support_level='explicit_outcome', proposition='夜拍一定更清晰')
        raw['surfaces']['image:0.caption'].append(row)
    original_validator = product_claim_review.validate_product_claim_review
    passed_evidence = []

    def inspect_validator(*args, **kwargs):
        passed_evidence.extend(deepcopy(kwargs.get('asset_evidence', [])))
        return original_validator(*args, **kwargs)

    monkeypatch.setattr(product_claim_review, 'validate_product_claim_review', inspect_validator)
    audit = asyncio.run(model.verify(result, bundle))
    assert passed_evidence == evidence
    schema = captured[0]['product_claim_schema']
    expected_ids = ['camera', *[row['id'] for row in evidence]]
    assert schema['$defs']['ProductClaim']['properties']['evidence_ids']['items']['enum'] == expected_ids
    assert schema['$defs']['EvidenceQuote']['properties']['evidence_id']['enum'] == expected_ids
    assert captured[0]['asset_evidence'] == evidence
    typed = result.strategy['product_claim_review']
    assert typed['coverage']['image:0.caption'] == 'claims_listed'
    assert typed['verdict'] == ('unsupported' if misuse_as_performance else 'supported')
    assert [row['evidence_quotes'][0]['quote'] for row in typed['claims']] == [row['text'] for row in evidence]
    assert any(item.severity == 'error' for item in audit) is misuse_as_performance
