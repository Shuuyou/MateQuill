"""Production clause contract: exact identity, actual bindings, no guessed support."""
from copy import deepcopy
import pytest

from app.design_copy import VisualCopyCard, visual_copy_audit_schema
from test_design_copy import ScriptedLLM, run, card


class StrictAuditor(ScriptedLLM):
    supports_visual_copy_clause_contract = True


def proposal():
    return card(headline='顺路看一眼', caption='HUAWEI Mate90',
        supporting_lines=['实验室条件下测试20小时'], fact_ids=['fact-battery'], evidence_ids=['store-evidence-1'])


def review(approved=True):
    return {'checks': [{'id':'cover-1','approved':approved,'reason':'按实际原句及绑定资料逐项审查',
        'clauses': [
            {'quote':'顺路看一眼','kind':'invitation','evidence_ids':[],'verdict':'not_assertion'},
            {'quote':'HUAWEI Mate90','kind':'expression','evidence_ids':[],'verdict':'not_assertion'},
            {'quote':'实验室条件下测试20小时','kind':'product_claim','evidence_ids':['fact-battery'],'verdict':'supported'}]}]}


def execute(audit):
    writer=ScriptedLLM({'cards':[proposal()]})
    reviewer=StrictAuditor(audit)
    result=run(writer,auditor=reviewer)
    assert len(writer.calls)==1 and len(reviewer.calls)==1
    return result,reviewer


def test_strict_identity_and_supported_weight_use_the_same_single_audit_request():
    result,reviewer=execute(review())
    assert result['status']=='approved'
    contract=reviewer.calls[0]['payload']['card_binding_contracts'][0]
    assert contract['identity_labels']==['HUAWEI Mate90']
    assert contract['bound_fact_ids']==['fact-battery']
    assert reviewer.calls[0]['payload']['schema']['additionalProperties'] is False


@pytest.mark.parametrize('change', [
    {'kind':'product_claim'}, {'evidence_ids':['fact-battery']}, {'verdict':'supported'},
])
def test_inconsistent_pure_identity_classification_is_rejected_never_rewritten(change):
    audit=review();audit['checks'][0]['clauses'][1].update(change)
    result,_=execute(audit)
    assert result['status']=='rejected'
    assert result['audit_checks'][0]['clauses'][1]==audit['checks'][0]['clauses'][1]


@pytest.mark.parametrize('citations', [[], ['store-evidence-1'], ['fact-screen'], ['fact-battery','fact-battery'], ['invented-fact']])
def test_supported_capability_requires_actual_card_bound_fact_id(citations):
    audit=review();audit['checks'][0]['clauses'][2]['evidence_ids']=citations
    result,_=execute(audit)
    assert result['status']=='rejected'
    assert result['audit_checks'][0]['clauses'][2]['evidence_ids']==citations


def test_prod_flag_requires_all_three_public_strings_and_clauses():
    missing=review();missing['checks'][0].pop('clauses')
    assert execute(missing)[0]['status']=='rejected'
    incomplete=review();incomplete['checks'][0]['clauses'].pop(0)
    assert execute(incomplete)[0]['status']=='rejected'


def test_product_statement_cannot_use_identity_branch_or_reviewer_boolean_to_skip_support():
    written=proposal();written['supporting_lines']=['HUAWEI Mate90支持所有课程软件']
    writer=ScriptedLLM({'cards':[written]})
    check=review();check['checks'][0]['clauses'][2].update(
        quote=written['supporting_lines'][0],evidence_ids=[],verdict='unsupported')
    reviewer=StrictAuditor(check)
    result=run(writer,auditor=reviewer)
    assert result['status']=='rejected'
    assert reviewer.calls[0]['payload']['card_binding_contracts'][0]['identity_labels']==['HUAWEI Mate90']


def test_false_approval_stays_rejected_even_with_well_formed_identity_and_real_citations():
    result,_=execute(review(approved=False))
    assert result['status']=='rejected'
    assert result['audit_checks'][0]['approved'] is False


def test_quote_must_remain_verbatim_instead_of_normalizing_a_new_claim_into_the_original():
    audit=review();audit['checks'][0]['clauses'][2]['quote']='测试结果超过20小时'
    assert execute(audit)[0]['status']=='rejected'


def test_actual_output_schema_locks_per_card_ids_and_identity_speech_act():
    first=VisualCopyCard.model_validate(proposal())
    second=VisualCopyCard.model_validate(card('second',fact_ids=['fact-screen']))
    schema=visual_copy_audit_schema([first,second],'HUAWEI Mate90')
    first_schema,second_schema=schema['properties']['checks']['items']['oneOf']
    assert first_schema['properties']['id']['const']=='cover-1'
    assert second_schema['properties']['id']['const']=='second'
    branches=first_schema['properties']['clauses']['items']['oneOf']
    identity=next(branch for branch in branches if branch['properties']['quote'].get('enum'))
    assert identity['properties']['kind']['enum']==['expression']
    assert identity['properties']['verdict']['enum']==['not_assertion']
    supported=next(branch for branch in branches if branch['properties']['kind']['enum']==['product_claim'])
    assert supported['properties']['evidence_ids']['items']['enum']==['fact-battery']
    assert supported['properties']['evidence_ids']['minItems']==1
    assert first_schema['required']==['id','approved','reason','clauses']
