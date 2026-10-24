from copy import deepcopy

import pytest

from app.claim_conditions import (condition_exemption_matches, condition_surface_map, validated_condition_exemptions,
                                  inspect_condition_exemptions, missing_condition_candidates)


def example():
    result = {'blocks': [{'text': '手机支持WLAN连接。', 'fact_ids': ['f1']}],
              'creative_plan': {'cards': [{'id': 'c1', 'headline': '连接', 'body': '手机支持WLAN连接。', 'fact_ids': ['f1']}],
                                'test_variants': [{'title': '连接', 'cover_copy': '看看连接'}]}}
    bundle = {'facts': [{'id': 'f1', 'qualifiers': ['需要相应的路由器支持。', '卫星消息首次使用需联网激活。']}]}
    data = {'verdict': 'supported', 'audit': [], 'condition_scope': [{
        'surface': 'body:0', 'fact_id': 'f1', 'qualifier': '卫星消息首次使用需联网激活。',
        'claim_quote': '手机支持WLAN连接。', 'applicable': False,
        'reason': '整段仅陈述WLAN连接，复合来源中的卫星消息条件与该主张无关。'}]}
    return data, result, bundle


def test_exact_irrelevant_composite_condition_exempt_only_target():
    data, result, bundle = example()
    original = deepcopy(bundle)
    exemptions = validated_condition_exemptions(data, result, bundle)
    assert len(exemptions) == 1
    assert bundle == original
    assert condition_exemption_matches(exemptions, surface='body:0', fact_id='f1',
        qualifier='卫星消息首次使用需联网激活。', text='手机支持WLAN连接。')
    assert not condition_exemption_matches(exemptions, surface='body:0', fact_id='f1',
        qualifier='需要相应的路由器支持。', text='手机支持WLAN连接。')
    assert not condition_exemption_matches(exemptions, surface='card:c1', fact_id='f1',
        qualifier='卫星消息首次使用需联网激活。', text='手机支持WLAN连接。')


@pytest.mark.parametrize('mutation', ['missing', 'error', 'unsupported', 'extra', 'unknown_fact', 'unknown_qualifier',
                                    'unknown_surface', 'uncited_fact', 'short_quote', 'stale_quote', 'duplicate',
                                    'applicable_true', 'applicable_string', 'applicable_zero', 'wrong_scope_type', 'bad_citation_type'])
def test_fail_closed_on_missing_or_invalid_scope(mutation):
    data, result, bundle = example()
    row = data['condition_scope'][0]
    if mutation == 'missing':
        del data['condition_scope']
    elif mutation == 'error':
        data['audit'] = [{'severity': 'error', 'category': 'semantic', 'message': '未支持'}]
    elif mutation == 'unsupported':
        data['verdict'] = 'unsupported'
    elif mutation == 'extra':
        row['ignore_all_conditions'] = True
    elif mutation == 'unknown_fact':
        row['fact_id'] = 'f2'
    elif mutation == 'unknown_qualifier':
        row['qualifier'] = '其他条件'
    elif mutation == 'unknown_surface':
        row['surface'] = 'body:2'
    elif mutation == 'uncited_fact':
        result['blocks'][0]['fact_ids'] = []
    elif mutation == 'short_quote':
        row['claim_quote'] = 'WLAN'
    elif mutation == 'stale_quote':
        result['blocks'][0]['text'] += '同时支持卫星消息。'
    elif mutation == 'duplicate':
        data['condition_scope'].append(deepcopy(row))
    elif mutation == 'applicable_true':
        row['applicable'] = True
    elif mutation == 'applicable_string':
        row['applicable'] = 'false'
    elif mutation == 'applicable_zero':
        row['applicable'] = 0
    elif mutation == 'bad_citation_type':
        result['blocks'][0]['fact_ids'] = 'f123'
    elif mutation == 'wrong_scope_type':
        data['condition_scope'] = {'body:0': False}
    assert validated_condition_exemptions(data, result, bundle) == []


def test_one_bad_scope_invalidates_all_exemptions():
    data, result, bundle = example()
    bad = deepcopy(data['condition_scope'][0])
    bad['qualifier'] = '不存在的条件'
    data['condition_scope'].append(bad)
    assert validated_condition_exemptions(data, result, bundle) == []


def test_card_binding_uses_whole_actual_public_card_not_one_unrelated_sentence():
    data, result, bundle = example()
    data['condition_scope'][0]['surface'] = 'card:c1'
    assert validated_condition_exemptions(data, result, bundle) == []
    data['condition_scope'][0]['claim_quote'] = '连接\n手机支持WLAN连接。'
    exemptions = validated_condition_exemptions(data, result, bundle)
    assert len(exemptions) == 1
    assert not condition_exemption_matches(exemptions, surface='card:c1', fact_id='f1',
        qualifier='卫星消息首次使用需联网激活。', text='连接\n手机支持WLAN连接，同时支持卫星消息。')


def test_uncited_variants_cannot_exempt_a_fact():
    data, result, bundle = example()
    data['condition_scope'][0].update(surface='variant:0', claim_quote='连接\n看看连接')
    assert validated_condition_exemptions(data, result, bundle) == []


def test_duplicate_card_identity_is_ambiguous():
    data, result, bundle = example()
    result['creative_plan']['cards'].append(deepcopy(result['creative_plan']['cards'][0]))
    assert 'card:c1' not in condition_surface_map(result)


def test_mixed_version_condition_stays_enforced_without_exact_exemption():
    data, result, bundle = example()
    bundle['facts'][0]['qualifiers'].append('需升级至HarmonyOS 6.1。')
    exemptions = validated_condition_exemptions(data, result, bundle)
    assert not condition_exemption_matches(exemptions, surface='body:0', fact_id='f1',
        qualifier='需升级至HarmonyOS 6.1。', text='手机支持WLAN连接。')


def test_stable_surface_quote_is_filled_then_bound_against_later_draft():
    data, result, bundle = example()
    data['condition_scope'][0]['claim_quote'] = ''
    exemptions, diagnostics = inspect_condition_exemptions(data, result, bundle)
    assert diagnostics == {'present': True, 'count': 1, 'accepted_count': 1}
    assert exemptions[0]['claim_quote'] == '手机支持WLAN连接。'
    assert not condition_exemption_matches(exemptions, surface='body:0', fact_id='f1',
        qualifier='卫星消息首次使用需联网激活。', text='手机支持卫星消息。')


def test_safe_diagnostics_distinguish_absent_output_from_bad_reference():
    data, result, bundle = example()
    del data['condition_scope']
    _, absent = inspect_condition_exemptions(data, result, bundle)
    assert absent['present'] is False and absent['count'] == 0
    data['condition_scope'] = [{'private_text': 'do not persist'}]
    _, invalid = inspect_condition_exemptions(data, result, bundle)
    assert invalid['rejection']['code'] == 'invalid_scope_schema'
    assert 'do not persist' not in str(invalid)
    assert all(set(row) == {'loc', 'type'} for row in invalid['rejection']['schema_errors'])


def test_missing_candidates_enumerate_conditions_without_preapproving_them():
    _, result, bundle = example()
    result['blocks'][0]['disclosures'] = ['需要相应的路由器支持。']
    candidates = missing_condition_candidates(result, bundle)
    body_candidates = [x for x in candidates if x['surface'] == 'body:0']
    assert len(body_candidates) == 1
    assert body_candidates[0]['qualifier'] == '卫星消息首次使用需联网激活。'
    assert body_candidates[0]['claim_quote'] == ''
    assert validated_condition_exemptions({'verdict': 'supported', 'audit': []}, result, bundle) == []
