from copy import deepcopy

import pytest

from app.product_claim_review import product_claim_surfaces, validate_product_claim_review
from app.product_claim_wire import decode_product_claim_wire, product_claim_wire_schema


SURFACES = {'title': '观察街角', 'body:0': '切长焦看远景，再切主摄拍身边。'}
IDS = ['camera']


def claim():
    return {'proposition': '可切长焦看远景', 'claim_type': 'affordance',
            'evidence_ids': ['camera'], 'evidence_quotes': [{'evidence_id': 'camera'}],
            'support_level': 'functional_entailment', 'verdict': 'supported',
            'reason': '已有长焦支持普通操作。'}


def wire():
    return {'surfaces': {'title': [], 'body:0': [claim()]}}


def test_schema_exact_groups_omitted_server_fields_and_evidence_enums():
    schema = product_claim_wire_schema(SURFACES, ['camera', 'battery', 'camera'])
    assert schema['required'] == ['surfaces']
    assert schema['additionalProperties'] is False
    groups = schema['properties']['surfaces']
    assert groups['required'] == list(SURFACES)
    assert groups['additionalProperties'] is False
    assert set(groups['properties']) == set(SURFACES)
    row = schema['$defs']['ProductClaim']
    span = schema['$defs']['EvidenceQuote']
    assert row['additionalProperties'] is False
    assert span['additionalProperties'] is False
    assert 'surface' not in row['properties'] and 'quote' not in row['properties']
    assert 'quote' not in span['properties']
    assert row['properties']['evidence_ids']['items']['enum'] == ['camera', 'battery']
    assert span['properties']['evidence_id']['enum'] == ['camera', 'battery']
    assert product_claim_wire_schema(SURFACES, [])['$defs']['EvidenceQuote']['properties']['evidence_id']['enum'] == []


@pytest.mark.parametrize('kind', ['missing', 'extra', 'alias'])
def test_exact_surface_boundary(kind):
    raw = wire()
    if kind == 'missing':
        del raw['surfaces']['title']
    elif kind == 'extra':
        raw['surfaces']['secret-unknown-key'] = []
    else:
        raw['surfaces']['body.0'] = raw['surfaces'].pop('body:0')
    with pytest.raises(ValueError, match='^product_claim_wire_surface_mismatch$'):
        decode_product_claim_wire(raw, SURFACES, IDS)


@pytest.mark.parametrize('surface', ['title', 'body:0', 'unknown-secret'])
def test_repeated_surface_is_rejected_even_if_matching_group(surface):
    raw = wire()
    raw['surfaces']['body:0'][0]['surface'] = surface
    with pytest.raises(ValueError, match='^product_claim_wire_claim_invalid$'):
        decode_product_claim_wire(raw, SURFACES, IDS)


@pytest.mark.parametrize('location', ['claim', 'evidence'])
@pytest.mark.parametrize('quote', ['', 'invented-secret-quote'])
def test_model_quote_slots_are_forbidden(location, quote):
    raw = wire()
    row = raw['surfaces']['body:0'][0]
    (row if location == 'claim' else row['evidence_quotes'][0])['quote'] = quote
    with pytest.raises(ValueError) as error:
        decode_product_claim_wire(raw, SURFACES, IDS)
    assert str(error.value) in {'product_claim_wire_claim_invalid', 'product_claim_wire_evidence_quote_invalid'}


def test_multiple_claims_empty_groups_and_text_judgments_preserved_without_mutation():
    raw = wire()
    second = claim()
    second.update(proposition='可切主摄拍身边', verdict='unsupported', reason='保留原判断。')
    raw['surfaces']['body:0'].append(second)
    original = deepcopy(raw)
    result = decode_product_claim_wire(raw, SURFACES, IDS)
    assert raw == original
    assert result['coverage'] == {'title': 'no_product_claim', 'body:0': 'claims_listed'}
    assert len(result['claims']) == 2
    for expected, actual in zip(raw['surfaces']['body:0'], result['claims']):
        assert actual['surface'] == 'body:0' and actual['quote'] == ''
        assert actual['evidence_quotes'] == [{'evidence_id': 'camera', 'quote': ''}]
        assert {key: actual[key] for key in expected if key != 'evidence_quotes'} == {
            key: value for key, value in expected.items() if key != 'evidence_quotes'}


def test_empty_review_and_empty_surface_inventory():
    assert decode_product_claim_wire({'surfaces': {'title': [], 'body:0': []}}, SURFACES, []) == {
        'claims': [], 'coverage': {'title': 'no_product_claim', 'body:0': 'no_product_claim'}}
    assert decode_product_claim_wire({'surfaces': {}}, {}, []) == {'claims': [], 'coverage': {}}


@pytest.mark.parametrize('location', ['id', 'span'])
def test_unknown_evidence_is_never_guessed(location):
    raw = wire()
    row = raw['surfaces']['body:0'][0]
    if location == 'id':
        row['evidence_ids'] = ['private-unknown']
    else:
        row['evidence_quotes'][0]['evidence_id'] = 'private-unknown'
    with pytest.raises(ValueError, match='^product_claim_wire_unknown_evidence$'):
        decode_product_claim_wire(raw, SURFACES, IDS)


@pytest.mark.parametrize('raw', [None, [], {'claims': [], 'coverage': {}},
                                 {'surfaces': {}, 'coverage': {}}, {'surfaces': []},
                                 {'surfaces': {'title': [], 'body:0': {}}}])
def test_invalid_shape_raises_safe_value_error(raw):
    with pytest.raises(ValueError, match='^product_claim_wire_'):
        decode_product_claim_wire(raw, SURFACES, IDS)


def test_invalid_typed_values_fail_without_echoing_payload():
    raw = wire()
    raw['surfaces']['body:0'][0]['verdict'] = 'private-invalid-verdict'
    with pytest.raises(ValueError, match='^product_claim_wire_value_invalid$'):
        decode_product_claim_wire(raw, SURFACES, IDS)


def test_aggregate_legacy_claim_limit_is_preserved():
    raw = {'surfaces': {'title': [claim() for _ in range(33)], 'body:0': [claim() for _ in range(32)]}}
    with pytest.raises(ValueError, match='^product_claim_wire_value_invalid$'):
        decode_product_claim_wire(raw, SURFACES, IDS)


def test_normalized_input_is_accepted_by_existing_validator_and_quotes_server_filled():
    draft = {'title': SURFACES['title'], 'blocks': [{'text': SURFACES['body:0']}]}
    normalized = decode_product_claim_wire(wire(), product_claim_surfaces(draft), IDS)
    result = validate_product_claim_review(normalized, draft,
        {'facts': [{'id': 'camera', 'value': '支持长焦及主摄拍照。'}]})
    assert result['claims'][0]['quote'] == SURFACES['body:0']
    assert result['claims'][0]['evidence_quotes'][0]['quote'] == '支持长焦及主摄拍照。'
    assert result['verdict'] == 'supported'
