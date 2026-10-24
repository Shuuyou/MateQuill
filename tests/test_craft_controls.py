from copy import deepcopy
import json

import pytest
from pydantic import ValidationError

from app.design.craft import build_craft_contract, check_craft_revision
from app.design.craft_controls import CraftEditInput, apply_craft_controls
from app.design.models import DesignPlan
from app.design.connectors import get_platform_connector


def fixture():
    rows=[{'id':'title','role':'headline','text_id':'approved','x':.1,'y':.08,'width':.8,'height':.12,'font_scale':.06},
          {'id':'product','role':'image','asset_id':'exact','x':.1,'y':.32,'width':.8,'height':.4}]
    plan=DesignPlan.model_validate({'brief':{'product':'Exact product','platform':get_platform_connector('moments').model_dump(),
        'source_run_id':'run','texts':{'approved':{'text':'批准文字','source_ids':['source']}},'asset_ids':['exact']},
        'selected_concept_id':'one','concepts':[{'id':'one','name':'Source-bound','visual_thesis':'One actual product',
        'production_method':'native_assembly','composition':deepcopy(rows),'pages':[{'id':'page','role':'cover','asset_id':'exact','text_ids':['approved'],'composition':deepcopy(rows)}]}],
        'provenance':{'source_digest':'original'}})
    assets={'exact':{'product':'Exact product','source_id':'source','sha256':'a'*64,'width':1200,'height':800,
                     'visual_role':'product_packshot','semantic_basis':'pixel_review_sha256'}}
    return plan,build_craft_contract(plan.brief,available_assets=assets)


def edit(plan,contract,**kwargs):
    return apply_craft_controls(plan,contract,page_id=kwargs.pop('page_id','page'),
        region_id=kwargs.pop('region_id','title'),values=kwargs.pop('values',{'letter_spacing':-1.5}),
        goal=kwargs.pop('goal','Improve the current hierarchy'),**kwargs)


def test_valid_typography_edit_changes_only_requested_target_and_preserves_parent_and_sources():
    plan,contract=fixture();before=deepcopy(plan.model_dump());locked=deepcopy(contract)
    revised,order=edit(plan,contract,values={'letter_spacing':-1.5,'font_weight':600})
    assert plan.model_dump()==before and contract==locked
    assert revised.concepts[0].pages[0].composition[0].letter_spacing==-1.5
    assert revised.concepts[0].pages[0].composition[0].font_weight==600
    assert revised.brief==plan.brief and revised.provenance==plan.provenance
    assert revised.concepts[0].pages[0].composition[1]==plan.concepts[0].pages[0].composition[1]
    assert revised.concepts[0].composition==plan.concepts[0].composition
    assert check_craft_revision(order,revised)['within_scope'] is True
    assert order['summary']['controls']==['letter_spacing','font_weight']
    assert 'base_plan' not in order['summary'] and 'summary' not in order['base_plan']['provenance']


def test_contact_shadow_edit_stays_in_native_image_controls_without_unlocking_or_replacing_source():
    plan,contract=fixture()
    revised,order=edit(plan,contract,region_id='product',values={'contact_shadow':{'center_x':.5,'center_y':1,'width':.8,'height':.025,'blur':6,'opacity':.18,'color':'#303030'}})
    target=revised.concepts[0].pages[0].composition[1]
    assert target.asset_id=='exact' and target.contact_shadow.blur==6
    assert check_craft_revision(order,revised)['within_scope'] is True
    assert order['locked_sources_and_copy']==contract['locks']


@pytest.mark.parametrize('values',[{}, {'asset_id':'other'}, {'text_id':'new'}, {'source_id':'forged'},
    {'locked':False}, {'x':'0.3'}, {'x':True}, {'font_weight':'700'}, {'letter_spacing':float('nan')},
    {'letter_spacing':100}, {'contact_shadow':{'blur':100}}, {'contact_shadow':{'color':'url(bad)'}},
    {'contact_shadow':{'extra':'payload'}}])
def test_unknown_keys_invalid_types_and_out_of_bounds_values_rejected(values):
    plan,contract=fixture();before=plan.model_dump()
    with pytest.raises((ValidationError,ValueError)):edit(plan,contract,values=values)
    assert plan.model_dump()==before


def test_wrong_control_role_missing_target_and_canvas_overlap_rejected():
    plan,contract=fixture()
    with pytest.raises(ValueError,match='type_requires_text'):
        edit(plan,contract,region_id='product',values={'font_scale':.04})
    with pytest.raises(ValueError,match='contact_shadow_requires_image'):
        edit(plan,contract,values={'contact_shadow':{}})
    with pytest.raises(ValueError,match='target_not_unique'):
        edit(plan,contract,page_id='missing')
    with pytest.raises(ValueError,match='target_not_unique'):
        edit(plan,contract,region_id='missing')
    with pytest.raises(ValidationError):edit(plan,contract,values={'x':.5})
    with pytest.raises(ValidationError):edit(plan,contract,values={'y':.4})


def test_work_order_checker_rejects_followon_change_to_other_content_or_source_binding():
    plan,contract=fixture();revised,order=edit(plan,contract)
    for tamper in ('source','text','other_region','provenance','other_concept_root'):
        changed=revised.model_dump()
        if tamper=='source':changed['brief']['asset_ids']=['other']
        elif tamper=='text':changed['brief']['texts']['approved']['text']='unapproved'
        elif tamper=='other_region':changed['concepts'][0]['pages'][0]['composition'][1]['opacity']=.5
        elif tamper=='provenance':changed['provenance']['external']='payload'
        else:changed['concepts'][0]['composition'][1]['asset_id']='other'
        assert check_craft_revision(order,changed)['within_scope'] is False


def test_input_rejects_whitespace_and_external_payload():
    with pytest.raises(ValidationError):CraftEditInput(page_id=' ',region_id='title',values={'z':1},goal='goal')
    with pytest.raises(ValidationError):CraftEditInput(page_id='page',region_id='title',values={'z':1},goal=' ')
    with pytest.raises(ValidationError):CraftEditInput(page_id='page',region_id='title',values={'z':1},goal='goal',base_plan={})


def test_legacy_composition_without_pages_uses_server_derived_cover_target():
    plan,contract=fixture();plan.concepts[0].pages=[]
    revised,order=edit(plan,contract,page_id='cover',values={'letter_spacing':-1})
    assert revised.concepts[0].composition[0].letter_spacing==-1
    assert check_craft_revision(order,revised)['within_scope'] is True
