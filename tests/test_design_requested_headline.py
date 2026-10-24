"""Current image-copy commission survives drafting, auditing and existing repair."""
import asyncio
from copy import deepcopy

from app.design_copy import prepare_design_copy


class Model:
    def __init__(self, *responses):
        self.responses = list(responses)
        self.calls = []

    async def _json(self, system, payload, temperature):
        self.calls.append({'system': system, 'payload': deepcopy(payload)})
        return deepcopy(self.responses.pop(0))


def card(title='旧帖子标题', identifier='cover', role='cover', **changes):
    return {'id': identifier, 'role': role, 'headline': title, 'caption': '',
            'fact_ids': [], 'evidence_ids': [], **changes}


def request(text='把工作放一边', identifier='cover', field='headline', quote=None):
    return {'card_id': identifier, 'field': field, 'text': text,
            'source_quote': quote or '图上短字“把工作放一边”'}


def audit(*ids, approved=True):
    return {'checks': [{'id': i, 'approved': approved, 'reason': '已检查候选原句'} for i in ids]}


def run(model, instruction='图上短字“把工作放一边”', **changes):
    return asyncio.run(prepare_design_copy({'product': 'HUAWEI MateBook', 'platform': '朋友圈'},
        {'title': '旧帖子标题', 'facts': []}, brand_identity={}, store_context={},
        llm=model, instruction=instruction, max_pages=2, **changes))


def test_model_failures_retain_safe_stage_and_code_without_raw_provider_response():
    from app.llm import ModelCallError
    class Fails:
        async def _json(self,*a,**k):raise ModelCallError('invalid_model_json',http_status=502)
    result=run(Fails())
    assert result['status']=='unavailable' and result['reason']=='ModelCallError'
    assert result['model_failure']=={'phase':'writer','code':'invalid_model_json','http_status':502}


def test_current_requested_text_is_independent_of_source_post_title_and_audited():
    proposed = card('把工作放一边')
    model = Model({'cards': [proposed], 'requested_copy': [request()]}, audit('cover'))
    result = run(model)
    assert result['status'] == 'approved' and result['cards'] == [proposed]
    assert len(model.calls) == 2
    writer, reviewer = [c['payload'] for c in model.calls]
    assert writer['context']['existing_copy']['title'] == '旧帖子标题'
    assert writer['context']['current_copy_commission']['instruction'] == '图上短字“把工作放一边”'
    assert reviewer['current_copy_commission']['requested_copy'] == [request()]
    assert 'requested_copy' not in reviewer['candidate']


def test_declared_request_mismatch_cannot_be_approved_or_force_overwritten():
    proposed = card()
    model = Model({'cards': [proposed], 'requested_copy': [request()]}, audit('cover'))
    result = run(model)
    assert result['status'] == 'rejected' and result['cards'] == []
    assert result['proposed_cards'] == [proposed]
    assert len(model.calls) == 2


def test_auditor_sees_original_instruction_even_when_writer_omits_requested_copy():
    model = Model({'cards': [card()]}, audit('cover', approved=False))
    result = run(model)
    assert result['status'] == 'rejected'
    commission = model.calls[1]['payload']['current_copy_commission']
    assert commission == {'instruction': '图上短字“把工作放一边”', 'requested_copy': []}
    assert '漏记' in model.calls[1]['system']


def test_quoted_reference_is_not_extracted_or_forced_as_headline():
    instruction = '参考“旧帖子标题”的气氛，别直接照写；图标题另想。'
    proposed = card('今天先合上')
    model = Model({'cards': [proposed]}, audit('cover'))
    result = run(model, instruction)
    assert result['status'] == 'approved' and result['cards'] == [proposed]
    assert model.calls[1]['payload']['current_copy_commission']['requested_copy'] == []


def test_requested_text_does_not_bypass_fact_audit():
    instruction = '图上写“全平台永久免费”'
    demanded = request('全平台永久免费', quote=instruction)
    model = Model({'cards': [card('全平台永久免费')], 'requested_copy': [demanded]},
                  audit('cover', approved=False))
    result = run(model, instruction)
    assert result['status'] == 'rejected' and len(model.calls) == 2


def test_existing_repair_retains_original_commission_and_budget():
    wrong = {'cards': [card()], 'requested_copy': [request()]}
    fixed = {'cards': [card('把工作放一边')], 'requested_copy': [request()]}
    model = Model(wrong, audit('cover'), fixed, audit('cover'))
    result = run(model, repair_rejections=True)
    assert result['status'] == 'approved'
    assert result['cards'][0]['headline'] == '把工作放一边'
    assert len(model.calls) == 4 and not model.responses
    assert model.calls[3]['payload']['current_copy_commission']['instruction'] == '图上短字“把工作放一边”'
    assert 'copy_repair_history' in result


def test_request_must_be_located_in_actual_current_commission():
    model = Model({'cards': [card('别的标题')], 'requested_copy': [request('别的标题', quote='不存在的委托：别的标题')]})
    result = run(model)
    assert result['status'] == 'unavailable' and len(model.calls) == 1


def test_requests_are_bound_to_each_card_and_field():
    instruction = '封面图上短字“把工作放一边”；第二张说明“周末见”。'
    requests = [request(), request('周末见', 'detail', 'caption', '第二张说明“周末见”')]
    cards = [card('把工作放一边'), card('看看窗外', 'detail', 'detail', caption='周末见')]
    model = Model({'cards': cards, 'requested_copy': requests}, audit('cover', 'detail'))
    result = run(model, instruction)
    assert result['status'] == 'approved' and result['cards'] == cards
    assert len(model.calls) == 2
