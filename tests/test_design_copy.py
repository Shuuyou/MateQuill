"""Independent-copy admission and evidence boundaries, using no network/provider."""
import asyncio
from copy import deepcopy

import pytest

from app.design_copy import prepare_design_copy


class ScriptedLLM:
    def __init__(self, *responses):
        self.responses = list(responses)
        self.calls = []

    async def _json(self, system, payload, temperature=.2):
        self.calls.append({'system': system, 'payload': deepcopy(payload), 'temperature': temperature})
        if not self.responses:
            raise AssertionError('unexpected extra model request')
        response = self.responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return deepcopy(response)


def card(identifier='cover-1', **changes):
    result = {'id': identifier, 'role': 'cover', 'headline': '带上常用照片来看看', 'caption': '比较屏幕与握持感',
              'fact_ids': [], 'evidence_ids': []}
    result.update(changes)
    return result


def audit(*identifiers, approved=True):
    return {'checks': [{'id': identifier, 'approved': approved,
                       'reason': '只邀请读者观察已提供产品，不承诺服务或功能'} for identifier in identifiers]}


def run(llm, **changes):
    arguments = {'task': {'product': 'HUAWEI Mate90', 'audience': '通勤者', 'platform': 'xiaohongshu'},
        'record': {'title': '从日常任务开始体验', 'facts': [
            {'id': 'fact-screen', 'product': 'HUAWEI Mate90', 'label': '屏幕参数', 'value': '屏幕参数A',
             'qualifiers': ['仅适用于指定配置'], 'quote': '屏幕参数A，具体配置以所购版本为准'},
            {'id': 'fact-battery', 'product': 'HUAWEI Mate90', 'label': '测试数据', 'value': '20小时',
             'qualifiers': ['实验室条件'], 'quote': '实验室条件下的测试结果'}]},
        'brand_identity': {'brand': 'HUAWEI', 'principles': ['准确产品与清楚信息']},
        'store_context': {'reader_task': '比较自己的常用照片', 'evidence': [
            {'id': 'store-evidence-1', 'kind': 'public_place', 'text': '附近存在公开办公园区'},
            {'source_id': 'source-place-2', 'kind': 'public_place', 'text': '公开门店位置记录'}]},
        'llm': llm}
    arguments.update(changes)
    return asyncio.run(prepare_design_copy(**arguments))


def test_new_short_copy_is_admitted_only_after_separate_audit_request():
    proposed = card()
    model = ScriptedLLM({'cards': [proposed]}, audit('cover-1'))
    result = run(model)
    assert result['status'] == 'approved'
    assert result['cards'] == [proposed]
    assert result['audit']['completed']
    assert len(model.calls) == 2
    assert model.calls[0]['temperature'] == .65 and model.calls[1]['temperature'] == .1
    assert '独立事实与门店身份审稿员' in model.calls[1]['system']
    assert model.calls[1]['payload']['candidate']['cards'] == [proposed]
    reviewed = model.calls[1]['payload']['evidence']
    assert reviewed['facts'] == model.calls[0]['payload']['context']['facts']
    assert reviewed['store_context']['evidence'] == []  # No geographic claim was cited.
    assert 'reader_task' not in reviewed['store_context']
    assert len(result['source_digest']) == 64


def test_image_editor_receives_actual_block_body_to_preserve_the_existing_idea():
    model = ScriptedLLM({'cards': [card()]}, audit('cover-1'))
    result = run(model, record={'title': 'A concrete comparison',
        'blocks': [{'text': 'Compare three fields of view during a short break.'}], 'facts': []})
    assert result['status'] == 'approved'
    assert model.calls[0]['payload']['context']['existing_copy']['blocks'] == [
        'Compare three fields of view during a short break.']


def test_reviewed_multi_view_subject_is_not_reduced_to_one_model_observation():
    model = ScriptedLLM({'cards': [card()]}, audit('cover-1'))
    run(model, record={'title': 'Existing idea', 'facts': [],
        'asset_observations': {'subject': {'view': 'rear'}},
        'assets': [{'id': 'subject', 'visible_views': ['front', 'rear'],
                    'visible_views_basis': 'pixel_review_sha256'}]})
    context = model.calls[0]['payload']['context']
    assert context['available_product_views'] == ['front', 'rear']
    assert context['unavailable_visual_tasks'] == []


@pytest.mark.parametrize('changes', [{'fact_ids': ['unknown-fact']}, {'evidence_ids': ['unknown-evidence']}])
def test_unknown_claim_references_fail_before_audit(changes):
    model = ScriptedLLM({'cards': [card(**changes)]})
    result = run(model)
    assert result['status'] == 'unavailable' and result['cards'] == []
    assert len(model.calls) == 1
    assert 'audit' not in result


def test_duplicate_card_ids_fail_before_audit():
    model = ScriptedLLM({'cards': [card(), card()]})
    result = run(model)
    assert result['status'] == 'unavailable' and result['cards'] == []
    assert len(model.calls) == 1


@pytest.mark.parametrize('audit_result', [
    None, {}, {'checks': []}, {'checks': [{'id': 'other', 'approved': True, 'reason': 'okay'}]},
    {'checks': [{'id': 'cover-1', 'approved': False, 'reason': '条件被省略'}]},
    {'checks': [{'id': 'cover-1', 'approved': 'true', 'reason': 'not boolean'}]},
    {'checks': [{'id': 'cover-1', 'approved': True, 'reason': ''}]},
    {'checks': [{'id': 'cover-1', 'approved': True}]},
    {'checks': [{'id': 'cover-1', 'approved': True, 'reason': 'ok'},
                {'id': 'cover-1', 'approved': True, 'reason': 'duplicate'}]},
])
def test_missing_rejected_or_invalid_audit_cannot_authorize_copy(audit_result):
    model = ScriptedLLM({'cards': [card()]}, audit_result)
    result = run(model)
    assert result['status'] == 'rejected' and result['cards'] == []
    assert len(model.calls) == 2
    assert 'audit' not in result


def test_partial_audit_of_multiple_cards_does_not_authorize_any_card():
    proposed = [card(), card('detail-1', role='detail', headline='看清一个真实细节')]
    model = ScriptedLLM({'cards': proposed}, audit('cover-1'))
    result = run(model)
    assert result['status'] == 'rejected' and result['cards'] == []


def test_valid_evidence_source_id_alias_is_accepted():
    proposed = card(evidence_ids=['source-place-2'])
    model = ScriptedLLM({'cards': [proposed]}, audit('cover-1'))
    result = run(model)
    assert result['status'] == 'approved'
    assert result['cards'][0]['evidence_ids'] == ['source-place-2']


def test_unbound_numeric_product_claim_fails_before_audit():
    model = ScriptedLLM({'cards': [card(headline='续航达到20小时')]})
    result = run(model)
    assert result['status'] == 'unavailable' and result['cards'] == []
    assert len(model.calls) == 1


def test_exact_server_product_name_digits_do_not_become_performance_claim():
    proposed = card(headline='HUAWEI Mate90握感如何')
    model = ScriptedLLM({'cards': [proposed]}, audit('cover-1'))
    result = run(model)
    assert result['status'] == 'approved'
    assert len(model.calls) == 2


def test_bound_claim_keeps_exact_conditions_in_both_requests():
    proposed = card(headline='测试数据20小时', caption='实验室条件', fact_ids=['fact-battery'])
    model = ScriptedLLM({'cards': [proposed]}, audit('cover-1'))
    result = run(model)
    assert result['status'] == 'approved'
    writer_fact = next(row for row in model.calls[0]['payload']['context']['facts'] if row['id'] == 'fact-battery')
    auditor_fact = next(row for row in model.calls[1]['payload']['evidence']['facts'] if row['id'] == 'fact-battery')
    assert writer_fact == auditor_fact
    assert auditor_fact['qualifiers'] == ['实验室条件']
    assert auditor_fact['quote'] == '实验室条件下的测试结果'
    assert model.calls[1]['payload']['candidate']['cards'][0]['caption'] == '实验室条件'
    assert '条件不可省略' in model.calls[0]['system']
    assert '条件遗漏则false' in model.calls[1]['system']


def test_condition_omission_rejected_by_independent_audit_is_not_repaired_or_guessed():
    proposed = card(headline='测试数据20小时', fact_ids=['fact-battery'])
    model = ScriptedLLM({'cards': [proposed]}, {'checks': [{'id': 'cover-1', 'approved': False,
        'reason': '省略实验室条件，不能作为普遍续航承诺'}]})
    result = run(model)
    assert result['status'] == 'rejected' and result['cards'] == []
    assert len(model.calls) == 2


@pytest.mark.parametrize('responses,expected_calls', [
    ((RuntimeError('CANARY_SECRET'),), 1),
    (({'cards': [card()]}, RuntimeError('CANARY_SECRET')), 2),
])
def test_provider_failure_never_synthesizes_approval_or_leaks_message(responses, expected_calls):
    model = ScriptedLLM(*responses)
    result = run(model)
    assert result['status'] == 'unavailable' and result['cards'] == []
    assert 'CANARY_SECRET' not in str(result)
    assert len(model.calls) == expected_calls


def test_missing_model_returns_unavailable_without_manufactured_audit():
    result = run(None)
    assert result == {'status': 'unavailable', 'cards': []}


def test_digest_tracks_evidence_changes_and_does_not_depend_on_auditor_prose():
    first = run(ScriptedLLM({'cards': [card()]}, audit('cover-1')))
    second = run(ScriptedLLM({'cards': [card()]}, audit('cover-1')),
                 store_context={'reader_task': '比较另一项任务', 'evidence': []})
    assert first['source_digest'] != second['source_digest']


def test_schema_repair_is_bounded_and_still_requires_independent_audit():
    model = ScriptedLLM({'cards': [card(headline='很长的标题' * 8)]},
                        {'cards': [card()]}, audit('cover-1'))
    result = run(model)
    assert result['status'] == 'approved'
    assert len(model.calls) == 3
    assert model.calls[1]['payload']['repair_request']['errors']
    assert 'candidate' in model.calls[2]['payload']


def test_schema_repair_does_not_guess_a_success_after_two_invalid_proposals():
    model = ScriptedLLM({'cards': [card(role='invalid')]}, {'cards': [card(role='invalid')]})
    result = run(model)
    assert result['status'] == 'unavailable' and not result['cards']
    assert result['validation_errors'][0]['loc'] == ['cards', 0, 'role']
    assert len(model.calls) == 2


def test_server_product_name_alias_is_not_a_performance_measurement():
    model = ScriptedLLM({'cards': [card(headline='Mate90握在手里怎么样', caption='')]}, audit('cover-1'))
    assert run(model, task={'product': 'HUAWEI Mate 90'})['status'] == 'approved'


def test_single_poster_does_not_fail_on_an_unused_long_detail_caption():
    model = ScriptedLLM({'cards': [card(), card('detail-2', role='detail', caption='完整适用条件' * 20)]}, audit('cover-1'))
    result = run(model, max_pages=1)
    assert result['status'] == 'approved' and len(result['cards']) == 1
    assert len(model.calls) == 2


def test_two_invalid_details_do_not_authorize_truncated_claims_or_lose_valid_cover():
    value = {'cards': [card(), card('detail-2', role='detail', caption='完整适用条件' * 20)]}
    model = ScriptedLLM(value, value, audit('cover-1'))
    result = run(model)
    assert result['status'] == 'approved' and [row['id'] for row in result['cards']] == ['cover-1']


def test_opt_in_audit_repair_rewrites_rejected_claim_then_requires_new_independent_approval():
    rejected = card(headline='测试数据20小时', fact_ids=['fact-battery'])
    feedback = {'checks': [{'id': 'cover-1', 'approved': False,
                           'reason': '遗漏实验室条件，不能写成普遍续航承诺'}]}
    revised = card(headline='下班带什么轻装出门', caption='看一眼随身握持的感觉')
    model = ScriptedLLM({'cards': [rejected]}, feedback,
                        {'cards': [revised]}, audit('cover-1'))
    result = run(model, repair_rejections=True)
    assert result['status'] == 'approved' and result['cards'] == [revised]
    assert result['audit']['completed'] is True
    assert result['copy_repair_history'] == feedback['checks']
    assert len(model.calls) == 4
    rewrite = model.calls[2]['payload']['context']
    assert feedback['checks'][0]['reason'] in rewrite['design_instruction']
    assert rewrite['facts'] == model.calls[0]['payload']['context']['facts']
    assert rewrite['store_context'] == model.calls[0]['payload']['context']['store_context']
    assert model.calls[1]['payload']['candidate']['cards'] == [rejected]
    assert model.calls[3]['payload']['candidate']['cards'] == [revised]
    assert '独立事实与门店身份审稿员' in model.calls[3]['system']
    assert model.calls[3]['temperature'] == .1


def test_opt_in_audit_repair_stops_after_second_rejection_without_authorizing_either_candidate():
    first = card(headline='测试数据20小时', fact_ids=['fact-battery'])
    second = card(headline='测试数据20小时', caption='天天都能达到', fact_ids=['fact-battery'])
    rejection = {'checks': [{'id': 'cover-1', 'approved': False, 'reason': '缺少实验室条件'}]}
    model = ScriptedLLM({'cards': [first]}, rejection, {'cards': [second]}, rejection)
    result = run(model, repair_rejections=True)
    assert result['status'] == 'rejected' and result['cards'] == []
    assert 'audit' not in result
    assert result['copy_repair_history'] == rejection['checks']
    assert result['audit_checks'] == rejection['checks']
    assert result['proposed_cards'] == [second]
    assert len(model.calls) == 4 and not model.responses


def test_explicit_independent_auditor_receives_review_while_writer_only_creates_copy():
    proposed = card()
    writer = ScriptedLLM({'cards': [proposed]})
    reviewer = ScriptedLLM(audit('cover-1'))
    result = run(writer, auditor=reviewer)
    assert result['status'] == 'approved' and result['cards'] == [proposed]
    assert len(writer.calls) == 1 and len(reviewer.calls) == 1
    assert '独立事实与门店身份审稿员' not in writer.calls[0]['system']
    assert '独立事实与门店身份审稿员' in reviewer.calls[0]['system']
    assert reviewer.calls[0]['payload']['candidate']['cards'] == [proposed]
    reviewed = reviewer.calls[0]['payload']['evidence']
    assert reviewed['facts'] == writer.calls[0]['payload']['context']['facts']
    assert reviewed['store_context']['evidence'] == []
    assert 'reader_task' not in reviewed['store_context']
    assert reviewer.calls[0]['temperature'] == .1


def test_geographic_invitation_keeps_bound_location_without_inheriting_unpublished_showroom_idea():
    location = {'id': 'geo-1', 'source_id': 'geo-1', 'kind': 'public_place',
                'text': '门店地址为教工路23号', 'url': 'https://example.com/store-location'}
    unrelated = {'id': 'unused-geo', 'kind': 'public_place', 'text': '另一处门店'}
    proposed = card(headline='逛到教工路，顺路看看', caption='进店沿边框摸一圈', evidence_ids=['geo-1'])
    writer = ScriptedLLM({'cards': [proposed]})
    reviewer = ScriptedLLM(audit('cover-1'))
    result = run(writer, auditor=reviewer, store_context={
        'store': {'name': '本店', 'location': '教工路23号'},
        'reader_task': '一层演示台已摆好样机，这是未经证实的编辑构想',
        'creative_opportunities': ['在假设的演示台旁比较手机'],
        'evidence': [location, unrelated], 'service_capabilities': []})
    assert result['status'] == 'approved'
    reviewed = reviewer.calls[0]['payload']['evidence']
    assert reviewed['store_context']['evidence'] == [location]
    assert reviewed['store_context']['store']['location'] == '教工路23号'
    assert reviewed['facts'] == writer.calls[0]['payload']['context']['facts']
    assert 'reader_task' not in reviewed['store_context']
    assert 'creative_opportunities' not in reviewed['store_context']
    assert '演示台' not in str(reviewed)
    assert reviewer.calls[0]['payload']['candidate']['cards'] == [proposed]


@pytest.mark.parametrize('facility', ['体验台', '柜台'])
@pytest.mark.parametrize('cited,kind,expected_status', [
    (False, 'authorized_store_record', 'rejected'),
    (True, 'editorial_idea', 'rejected'),
    (True, 'authorized_store_record', 'approved'),
])
def test_specific_facility_requires_bound_actual_record_even_when_model_audit_approves(facility, cited, kind, expected_status):
    record = {'id': 'facility-1', 'kind': kind,
              'quote': '本门店设有' + facility + '，信息由门店运营记录提供'}
    proposed = card(headline='到店看个细节', caption='在' + facility + '边沿机身边框摸一圈',
                    evidence_ids=['facility-1'] if cited else [])
    writer = ScriptedLLM({'cards': [proposed]})
    reviewer = ScriptedLLM(audit('cover-1'))
    result = run(writer, auditor=reviewer, store_context={
        'reader_task': '设想在' + facility + '旁讲解手机',
        'creative_opportunities': ['把具体设施画进场景'], 'evidence': [record]})
    assert result['status'] == expected_status
    assert len(reviewer.calls) == 1
    if expected_status == 'approved':
        assert result['cards'] == [proposed]
        assert reviewer.calls[0]['payload']['evidence']['store_context']['evidence'] == [record]
    else:
        assert result['cards'] == []
        assert result['audit_checks'][0]['approved'] is False
        assert facility in result['audit_checks'][0]['reason']


def test_writer_does_not_inherit_unrecorded_physical_set_from_editorial_scene():
    proposed = card()
    writer = ScriptedLLM({'cards': [proposed]}, audit('cover-1'))
    result = run(writer, task={'product': 'HUAWEI Mate90', 'scene': '在一层演示台观察机身', 'platform': 'xiaohongshu'},
        store_context={'store': {'name': '百脑汇华为店', 'location': '教工路23号'},
            'reader_task': '到演示台前比较手机', 'creative_opportunities': ['从柜台取出样机'],
            'local_signals': [{'text': '教工路商圈'}], 'evidence': []})
    assert result['status'] == 'approved'
    context = writer.calls[0]['payload']['context']
    assert context['task']['scene'] == ''
    assert not context['store_context']['reader_task']
    assert not context['store_context']['creative_opportunities']
    assert context['store_context']['store']['location'] == '教工路23号'
    assert context['store_context']['local_signals'] == [{'text': '教工路商圈'}]


def test_actual_facility_record_remains_available_to_writer_and_auditor():
    record = {'id': 'recorded-counter', 'kind': 'authorized_store_record', 'quote': '本店配有体验台'}
    proposed = card(caption='到体验台旁看一个细节', evidence_ids=['recorded-counter'])
    writer = ScriptedLLM({'cards': [proposed]}, audit('cover-1'))
    assert run(writer, store_context={'reader_task': '在体验台观察机身', 'evidence': [record]})['status'] == 'approved'
    assert writer.calls[0]['payload']['context']['store_context']['reader_task'] == '在体验台观察机身'
    assert writer.calls[1]['payload']['evidence']['store_context']['evidence'] == [record]


@pytest.mark.parametrize('bad_clause', [
    {'quote': '本店随到随试', 'kind': 'operational_claim', 'evidence_ids': [], 'verdict': 'unsupported'},
    {'quote': '到店看个细节', 'kind': 'location', 'evidence_ids': ['invented-id'], 'verdict': 'supported'},
    {'quote': '到店看个细节', 'kind': 'operational_claim', 'evidence_ids': [], 'verdict': 'not_assertion'},
])
def test_invalid_located_analysis_never_authorizes_copy_even_with_approved_boolean(bad_clause):
    proposed = card(headline='到店看个细节')
    verdict = audit('cover-1')
    verdict['checks'][0]['clauses'] = [bad_clause]
    result = run(ScriptedLLM({'cards': [proposed]}, verdict))
    assert result['status'] == 'rejected'
    assert result['audit_checks'][0]['clauses'] == [bad_clause]


def test_located_invitation_analysis_never_flips_model_rejection_to_approval():
    proposed = card(headline='到店看个细节')
    verdict = audit('cover-1', approved=False)
    verdict['checks'][0]['clauses'] = [{'quote': proposed['headline'], 'kind': 'invitation',
                                      'evidence_ids': [], 'verdict': 'not_assertion'}]
    result = run(ScriptedLLM({'cards': [proposed]}, verdict))
    assert result['status'] == 'rejected' and result['cards'] == []
    assert result['audit_checks'][0]['approved'] is False


def test_writer_schema_repair_still_uses_only_explicit_independent_auditor():
    bad = {'cards': [card(headline='太长的标题' * 10)]}
    fixed = card(headline='手指与机身怎样接触')
    writer = ScriptedLLM(bad, {'cards': [fixed]})
    reviewer = ScriptedLLM(audit('cover-1'))
    result = run(writer, auditor=reviewer)
    assert result['status'] == 'approved' and result['cards'] == [fixed]
    assert len(writer.calls) == 2 and len(reviewer.calls) == 1
    assert all('独立事实与门店身份审稿员' not in row['system'] for row in writer.calls)
    assert reviewer.calls[0]['payload']['candidate']['cards'] == [fixed]


def test_rejection_repair_recursion_preserves_same_independent_auditor_and_evidence():
    first = card(headline='测试数据20小时', fact_ids=['fact-battery'])
    revised = card(headline='掌心贴住哪一面', caption='用实际接触感受机身')
    rejected = {'checks': [{'id': 'cover-1', 'approved': False, 'reason': '遗漏实验室适用条件'}]}
    writer = ScriptedLLM({'cards': [first]}, {'cards': [revised]})
    reviewer = ScriptedLLM(rejected, audit('cover-1'))
    result = run(writer, auditor=reviewer, repair_rejections=True)
    assert result['status'] == 'approved' and result['cards'] == [revised]
    assert len(writer.calls) == 2 and len(reviewer.calls) == 2
    assert all('独立事实与门店身份审稿员' not in row['system'] for row in writer.calls)
    assert all('独立事实与门店身份审稿员' in row['system'] for row in reviewer.calls)
    assert reviewer.calls[0]['payload']['candidate']['cards'] == [first]
    assert reviewer.calls[1]['payload']['candidate']['cards'] == [revised]
    assert reviewer.calls[0]['payload']['evidence']['facts'] == reviewer.calls[1]['payload']['evidence']['facts']
    assert reviewer.calls[0]['payload']['evidence']['store_context'] == reviewer.calls[1]['payload']['evidence']['store_context']
    assert rejected['checks'][0]['reason'] in writer.calls[1]['payload']['context']['design_instruction']
    assert result['copy_repair_history'] == rejected['checks']


def test_independent_auditor_failure_cannot_fall_back_to_writer_self_approval():
    writer = ScriptedLLM({'cards': [card()]})
    reviewer = ScriptedLLM(RuntimeError('CANARY_SECRET'))
    result = run(writer, auditor=reviewer)
    assert result['status'] == 'unavailable' and result['cards'] == []
    assert len(writer.calls) == 1 and len(reviewer.calls) == 1
    assert 'CANARY_SECRET' not in str(result)


def test_two_independent_rejections_never_switch_to_writer_audit_or_authorize_copy():
    rejection = {'checks': [{'id': 'cover-1', 'approved': False, 'reason': '产品功能条件不完整'}]}
    writer = ScriptedLLM({'cards': [card()]}, {'cards': [card(headline='看一眼机身边缘')]})
    reviewer = ScriptedLLM(rejection, rejection)
    result = run(writer, auditor=reviewer, repair_rejections=True)
    assert result['status'] == 'rejected' and result['cards'] == []
    assert len(writer.calls) == 2 and len(reviewer.calls) == 2
    assert not writer.responses and not reviewer.responses
