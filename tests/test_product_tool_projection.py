import json
from pathlib import Path

import pytest
from fastapi import HTTPException

from app.gtm_agent.tools import project_product_evidence
from app.models import EvidenceBundle, Fact


def test_new_camera_subfields_remain_visible_beyond_old_first_sixteen_cut():
    rows = json.loads(Path('data/ecosystem_products.json').read_text(encoding='utf-8'))['products']
    phone = next(row for row in rows if '支持 5.5 倍光学变焦' in row['text'])
    bundle = EvidenceBundle(product=phone['name'], facts=[Fact.model_validate(row) for row in phone['facts']])
    zoom = next(fact for fact in bundle.facts if '变焦模式' in fact.label)
    assert bundle.facts.index(zoom) >= 16
    result = project_product_evidence(bundle)
    actual = next(row for row in result['facts'] if row['id'] == zoom.id)
    assert actual['value'] == zoom.value
    assert actual['qualifiers'] == zoom.qualifiers
    assert len(json.dumps(result, ensure_ascii=False)) < 16000


def test_overflow_indexes_real_ids_without_cutting_fact_or_condition():
    facts = [Fact(id=f'f{i}', product='Phone', label=f'Field {i}', value='完整原文' * 350,
        quote='完整原文' * 350, source_id='official', qualifiers=['需搭配指定附件。']) for i in range(40)]
    bundle = EvidenceBundle(product='Phone', facts=facts)
    result = project_product_evidence(bundle)
    assert len(result['facts']) < len(facts)
    assert result['remaining_fact_index']
    assert len(json.dumps(result, ensure_ascii=False)) <= 14500
    assert {row['id'] for row in result['facts']} | {row['id'] for row in result['remaining_fact_index']} == {fact.id for fact in facts}
    for row in result['facts']:
        original = next(f for f in facts if f.id == row['id'])
        assert row['value'] == original.value
        assert row['qualifiers'] == original.qualifiers
    selected = project_product_evidence(bundle, ['f39'])
    assert selected['facts'][0]['id'] == 'f39'
    assert selected['facts'][0]['value'] == facts[-1].value


def test_fact_reading_rejects_an_id_outside_actual_product_evidence():
    with pytest.raises(HTTPException) as exc:
        project_product_evidence(EvidenceBundle(product='Phone'), ['invented'])
    assert exc.value.status_code == 422
