"""Pending typed asset integration contract; run after the owner integrates it."""
from copy import deepcopy
import hashlib
import json

import pytest

from app import visual_assets
from app.asset_claim_evidence import build_asset_claim_evidence
from app.product_claim_review import product_claim_surfaces, validate_product_claim_review


@pytest.fixture
def asset_case(tmp_path, monkeypatch):
    content = b'authorized captured product image'
    path = tmp_path / 'asset.bin'
    path.write_bytes(content)
    digest = hashlib.sha256(content).hexdigest()
    registry = tmp_path / 'review.json'
    registry.write_text(json.dumps({digest: {'observed_title': '绿色机身斜置，圆形后摄',
        'semantic_tags': ['绿色', '背面'], 'visual_role': 'product_overview', 'usable': True}}), encoding='utf-8')
    monkeypatch.setattr(visual_assets, 'REVIEW_PATH', registry)
    draft = {'title': '产品资料', 'images': [
        {'asset_id': 'a', 'caption': '产品资料图：Mate80，绿色机身斜置，圆形后摄'},
        {'asset_id': 'b', 'caption': '另一张产品资料图'}]}
    bundle = {'facts': [{'id': 'f', 'value': '支持长焦拍照'}], 'assets': [
        {'id': 'a', 'product': 'Mate80', 'source_id': 's', 'verified': True,
         'sha256': digest, 'local_path': str(path), 'title': '绿色机身斜置，圆形后摄'}],
        'sources': [{'id': 's'}]}
    evidence = build_asset_claim_evidence(draft, bundle)
    return draft, bundle, evidence, registry


def review(draft, evidence_id, kind='asset_identity', level='authorized_asset', surface='image:0.caption'):
    coverage = {key: 'no_product_claim' for key in product_claim_surfaces(draft)}
    coverage[surface] = 'claims_listed'
    return {'coverage': coverage, 'claims': [{'surface': surface, 'quote': '',
        'proposition': '目录绑定Mate80' if kind == 'asset_identity' else '绿色机身与圆形后摄',
        'claim_type': kind, 'support_level': level, 'verdict': 'supported', 'reason': '引用对应来源。',
        'evidence_ids': [evidence_id], 'evidence_quotes': [{'evidence_id': evidence_id, 'quote': ''}]}]}


def test_identity_and_appearance_accept_only_their_respective_real_asset_records(asset_case):
    draft, bundle, evidence, _ = asset_case
    raw = review(draft, evidence[0]['id'])
    appearance = review(draft, evidence[1]['id'], 'asset_appearance', 'observed_pixels')['claims'][0]
    raw['claims'].append(appearance)
    original = deepcopy(raw)
    result = validate_product_claim_review(raw, draft, bundle, asset_evidence=evidence)
    assert raw == original
    assert result['verdict'] == 'supported'
    assert [row['claim_type'] for row in result['claims']] == ['asset_identity', 'asset_appearance']
    assert [row['evidence_quotes'][0]['quote'] for row in result['claims']] == [row['text'] for row in evidence]
    assert all(row['quote'] == draft['images'][0]['caption'] for row in result['claims'])


def test_asset_record_cannot_move_to_another_images_caption(asset_case):
    draft, bundle, evidence, _ = asset_case
    raw = review(draft, evidence[0]['id'], surface='image:1.caption')
    assert validate_product_claim_review(raw, draft, bundle, asset_evidence=evidence)['verdict'] == 'unsupported'


def test_identity_and_pixel_observation_cannot_substitute_for_each_other(asset_case):
    draft, bundle, evidence, _ = asset_case
    for identifier, kind, level in [(evidence[0]['id'], 'asset_appearance', 'observed_pixels'),
                                     (evidence[1]['id'], 'asset_identity', 'authorized_asset')]:
        raw = review(draft, identifier, kind, level)
        assert validate_product_claim_review(raw, draft, bundle, asset_evidence=evidence)['verdict'] == 'unsupported'


def test_asset_records_cannot_support_specs_capabilities_results_or_experiences(asset_case):
    draft, bundle, evidence, _ = asset_case
    for asset in evidence:
        for kind, level in [('spec', 'exact_spec'), ('affordance', 'functional_entailment'),
                            ('performance', 'explicit_outcome'), ('experience', 'actual_record')]:
            raw = review(draft, asset['id'], kind, level)
            assert validate_product_claim_review(raw, draft, bundle, asset_evidence=evidence)['verdict'] == 'unsupported'


def test_facts_outcomes_and_actual_records_cannot_impersonate_asset_evidence(asset_case):
    draft, bundle, evidence, _ = asset_case
    for identifier in ('f', 'outcome', 'record'):
        for kind, level in [('asset_identity', 'authorized_asset'), ('asset_appearance', 'observed_pixels')]:
            raw = review(draft, identifier, kind, level)
            result = validate_product_claim_review(raw, draft, bundle, asset_evidence=evidence,
                outcome_evidence=[{'id': 'outcome', 'text': '独立测试结果'}],
                actual_records=[{'id': 'record', 'text': '真实拍摄记录'}])
            assert result['verdict'] == 'unsupported'


def test_unknown_asset_reference_remains_a_boundary_error(asset_case):
    draft, bundle, evidence, _ = asset_case
    with pytest.raises(ValueError, match='unknown_product_claim_evidence'):
        validate_product_claim_review(review(draft, 'unknown-asset-evidence'), draft, bundle,
                                      asset_evidence=evidence)


def test_asset_evidence_quote_cannot_be_fabricated(asset_case):
    draft, bundle, evidence, _ = asset_case
    raw = review(draft, evidence[1]['id'], 'asset_appearance', 'observed_pixels')
    raw['claims'][0]['evidence_quotes'][0]['quote'] = '这台手机夜拍更清晰'
    with pytest.raises(ValueError, match='product_evidence_quote_mismatch'):
        validate_product_claim_review(raw, draft, bundle, asset_evidence=evidence)


def test_title_only_has_no_pixel_evidence_and_identity_still_cannot_support_appearance(asset_case):
    draft, bundle, _, registry = asset_case
    registry.write_text('{}', encoding='utf-8')
    evidence = build_asset_claim_evidence(draft, bundle)
    assert [row['kind'] for row in evidence] == ['asset_identity']
    raw = review(draft, evidence[0]['id'], 'asset_appearance', 'observed_pixels')
    assert validate_product_claim_review(raw, draft, bundle, asset_evidence=evidence)['verdict'] == 'unsupported'


@pytest.mark.parametrize('other_set', ['facts', 'outcome', 'actual_record'])
def test_cross_source_inventory_id_collision_is_rejected(asset_case, other_set):
    draft, bundle, evidence, _ = asset_case
    identifier = evidence[0]['id']
    kwargs = {'asset_evidence': evidence}
    fake = {'id': identifier, 'text': '不可覆盖资产来源的另一类材料'}
    if other_set == 'facts':
        bundle['facts'].append(fake)
    else:
        kwargs['outcome_evidence' if other_set == 'outcome' else 'actual_records'] = [fake]
    with pytest.raises(ValueError, match='^product_claim_evidence_id_collision$'):
        validate_product_claim_review(review(draft, identifier), draft, bundle, **kwargs)
