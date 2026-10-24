"""Copy craft is a bounded same-call handoff, never substitute fact evidence."""
import asyncio
from copy import deepcopy

import pytest

from app.design_copy import prepare_design_copy
from app.editorial_voice import build_editorial_voice, generation_instruction


class Model:
    def __init__(self, *responses):
        self.responses = list(responses)
        self.calls = []

    async def _json(self, system, payload, temperature):
        self.calls.append({'system': system, 'payload': deepcopy(payload)})
        return deepcopy(self.responses.pop(0))


def card():
    return {'id': 'cover', 'role': 'cover', 'headline': '展开这一页', 'caption': '',
            'fact_ids': [], 'evidence_ids': []}


def craft(**changes):
    return {'card_id': 'cover', 'proposition': '阅读开始于打开的一页',
            'product_or_task_anchor': '本次纸书与设备的阅读企划，不声称新增功能',
            'visual_payoff': '使用已有开合姿态与翻页形态呼应，不补造接口或内部结构',
            'alternatives': [{'headline': '展开这一页', 'tradeoff': '保留翻页动作与打开姿态的联系'},
                             {'headline': '再读一页', 'tradeoff': '阅读感直接但较少体现画面开合关系'}],
            'selected_headline': '展开这一页', 'selected_reason': '短句和本次图像关系一致',
            'headline_units': ['展开', '这一页'], 'readings': [], 'qualifier_plan': '', **changes}


def audit(approved=True):
    return {'checks': [{'id': 'cover', 'approved': approved, 'reason': '已审候选原句'}]}


def run(model, **changes):
    args = dict(brand_identity={}, store_context={}, llm=model, max_pages=1)
    args.update(changes)
    return asyncio.run(prepare_design_copy({'product': 'HUAWEI 合成设备', 'platform': '朋友圈'},
        {'title': '原帖标题', 'facts': []}, **args))


def test_craft_and_external_contract_share_existing_two_calls_without_becoming_public_copy():
    decision = craft()
    contract = {'visual_thesis': '准确开合姿态与阅读关系', 'production_method': 'native_assembly'}
    original = deepcopy(contract)
    model = Model({'cards': [card()], 'craft_decisions': [decision]}, audit())
    result = run(model, copy_craft_contract=contract)
    assert result['status'] == 'approved' and len(model.calls) == 2
    assert result['copy_craft'] == [decision] and result['cards'] == [card()]
    assert model.calls[0]['payload']['context']['copy_craft_contract'] == contract
    review = model.calls[1]['payload']
    assert review['copy_craft'] == [decision] and review['copy_craft_contract'] == contract
    assert review['candidate'] == {'cards': [card()]}
    assert contract == original


@pytest.mark.parametrize('changes', [
    {'selected_headline': '另一个标题'},
    {'headline_units': ['展开', '另一页']},
    {'card_id': 'nonexistent-card'},
])
def test_work_order_cannot_silently_change_selected_or_typeset_public_words(changes):
    model = Model({'cards': [card()], 'craft_decisions': [craft(**changes)]}, audit())
    result = run(model)
    assert result['status'] == 'rejected' and result['cards'] == []
    assert result['proposed_cards'] == [card()] and len(model.calls) == 2


def test_internal_qualifier_plan_is_not_accepted_as_published_claim_condition():
    decision = craft(qualifier_plan='把必要适用条件放工单里，图片不写')
    model = Model({'cards': [card()], 'craft_decisions': [decision]}, audit(False))
    result = run(model)
    assert result['status'] == 'rejected'
    assert '不是公开条件' in model.calls[1]['system']


def test_explicit_headline_request_is_not_replaced_by_internal_candidate_competition():
    demanded = {'card_id': 'cover', 'field': 'headline', 'text': '展开这一页',
                'source_quote': '图上写“展开这一页”'}
    model = Model({'cards': [card()], 'requested_copy': [demanded], 'craft_decisions': [craft()]}, audit())
    result = run(model, instruction='图上写“展开这一页”')
    assert result['status'] == 'rejected' and len(model.calls) == 2
    single = craft(alternatives=[{'headline': '展开这一页', 'tradeoff': '保留用户原句，不另改写'}])
    model = Model({'cards': [card()], 'requested_copy': [demanded], 'craft_decisions': [single]}, audit())
    assert run(model, instruction='图上写“展开这一页”')['status'] == 'approved'


def test_old_cards_remain_compatible_without_fabricated_craft_metadata():
    model = Model({'cards': [card()]}, audit())
    result = run(model)
    assert result['status'] == 'approved' and 'copy_craft' not in result
    assert model.calls[1]['payload']['copy_craft'] == []


def test_existing_repair_preserves_craft_contract_and_independent_review_budget():
    contract = {'intent': '字图共同完成阅读命题'}
    wrong = {'cards': [card()], 'craft_decisions': [craft(headline_units=['改变原句'])]}
    fixed = {'cards': [card()], 'craft_decisions': [craft()]}
    model = Model(wrong, audit(), fixed, audit())
    result = run(model, repair_rejections=True, copy_craft_contract=contract)
    assert result['status'] == 'approved' and len(model.calls) == 4
    assert model.calls[2]['payload']['context']['copy_craft_contract'] == contract
    assert model.calls[3]['payload']['copy_craft_contract'] == contract


def test_editorial_contract_allows_brand_and_store_craft_without_forced_style_or_extra_facts():
    brand = build_editorial_voice({'platform': '朋友圈'}, account_voice={'publisher_identity': 'brand'})
    store = build_editorial_voice({'platform': '朋友圈'}, account_voice={'publisher_identity': 'store'})
    assert brand['creative_craft'] == store['creative_craft']
    assert '字面层' in brand['creative_craft']['double_reading']
    assert '压缩语义而不删掉成立条件' in generation_instruction()
    assert brand['observation_atoms'] == [] and brand['selected_concept'] is None
