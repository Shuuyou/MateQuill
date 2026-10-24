"""Actual post/platform semantics drive design roles without becoming new claims."""
import asyncio
from copy import deepcopy
import json

import pytest

from app.design.native_visual import content_visual_commission, native_visual_contract
from app.design.publication import publication_strategy
from app.design.elements import plan_design_elements, generate_design_elements
from test_design_elements import fixture


def commission(post='电脑收工，给生活让一点位置', platform='moments', instruction=''):
    source={'run_id':'real-source','title':'今天的一个小动作','blocks':[{'text':post}]}
    brief={'platform':platform,'source_run_id':'real-source','instruction':instruction,
        'publication':publication_strategy(platform,source=source).model_dump()}
    return brief,source,content_visual_commission(brief,source)


def test_same_assets_do_not_erase_actual_post_or_platform_roles():
    _,_,first=commission()
    _,_,different=commission('路上留意招牌的明暗关系')
    _,_,xhs=commission(platform='xiaohongshu')
    assert first['sha256']!=different['sha256']!=xhs['sha256']
    assert first['post_meaning'][-1]['text']=='电脑收工，给生活让一点位置'
    assert first['publication_mode']=='moments_companion'
    assert xhs['publication_mode']=='xhs_title_body'
    assert first['semantic_input_only'] is True


def test_source_only_is_an_explicit_constraint_not_a_platform_default():
    _,_,normal=commission()
    _,_,restricted=commission(instruction='无叠字，仅做原生取景，不生成新的背景或装饰')
    assert normal['production_intent']=='content_led_design'
    assert restricted['production_intent']=='source_arrangement'
    assert restricted['overlay_policy']=='no_overlay_text'
    native=native_visual_contract({'platform':'moments','instruction':'围绕正文定制配图，制作原创设计',
        'store_context':{'photo_asset_ids':['real']}},assets={'real':{}})
    assert native['selected']=='editorial_concept'


def test_long_source_unit_is_omitted_whole_not_cropped_into_a_false_promise():
    text='有必要条件的完整原文'*250
    _,_,value=commission(text)
    assert value['omitted_whole_units']==1
    assert not any(text[:30] in row['text'] for row in value['post_meaning'])


class Director:
    enabled=True
    def __init__(self): self.calls=[]
    async def _json(self,system,payload):
        self.calls.append(payload)
        meaning=json.dumps(payload['context']['content_visual_commission']['post_meaning'],ensure_ascii=False)
        return {'motif':'空间由密到疏形成收束' if '收工' in meaning else '明暗交错形成一处观察',
                'material':'轻微纸面颗粒','light':'柔和侧光','spatial_instructions':'避让原生产品与文字范围',
                'native_layer_ids':payload['context']['native_layout']['native_layer_ids']}


def test_real_element_translation_gets_post_and_platform_but_image_request_gets_only_design_duties():
    plan,board,service,calls=fixture()
    brief,_,value=commission()
    plan['brief']=brief
    plan['provenance']={'content_visual_commission':value}
    director=Director()
    async def execute():
        direction=await plan_design_elements(plan,board,director)
        assert direction['direction_version']=='element_direction.v3'
        result=await generate_design_elements(plan,board,service,element_brief=direction)
        return direction,result
    direction,result=asyncio.run(execute())
    assert len(director.calls)==len(calls)==1
    assert director.calls[0]['context']['content_visual_commission']==value
    assert '空间由密到疏形成收束' in calls[0][0]
    assert value['post_meaning'][-1]['text'] not in calls[0][0]
    assert 'COPY-CANARY' not in calls[0][0] and 'data:image' not in calls[0][0]
    assert result['creative_commission_sha256']==value['sha256']


def test_changed_post_cannot_reuse_a_design_direction_bound_only_to_the_old_layout():
    plan,board,service,calls=fixture()
    brief,_,value=commission();plan['brief']=brief;plan['provenance']={'content_visual_commission':value}
    direction=asyncio.run(plan_design_elements(plan,board,Director()))
    plan['provenance']['content_visual_commission']=commission('路上留意招牌的明暗关系')[2]
    with pytest.raises(ValueError,match='direction_binding_mismatch'):
        asyncio.run(generate_design_elements(plan,board,service,element_brief=direction))
    assert calls==[]


def test_tampered_semantic_contract_is_not_accepted_as_a_new_brief():
    plan,board,_,_=fixture()
    brief,_,value=commission();plan['brief']=brief;plan['provenance']={'content_visual_commission':deepcopy(value)}
    plan['provenance']['content_visual_commission']['post_meaning'][0]['text']='改写但不更新绑定'
    with pytest.raises(ValueError,match='commission_hash_mismatch'):
        asyncio.run(plan_design_elements(plan,board,Director()))


def test_actual_native_planner_receives_a_post_commission_even_for_wordless_design():
    from app.design.planner import plan_design
    calls=[]
    class Model:
        async def _json(self,system,payload,**kwargs):
            calls.append(payload)
            assert payload['brief']['approved_copy']=={}
            assert 'production_method' in payload['output_contract']['concept']
            assert 'native_assembly' in payload['output_contract']['production_method']['enum']
            assert payload['content_visual_commission']['post_meaning'][-1]['text']=='电脑收工，给生活让一点位置'
            return {'selected_concept_id':'custom','concepts':[{'id':'custom','name':'收束与展开',
                'visual_thesis':'准确产品与空间节奏表达收束和让位','treatment':'editorial','production_method':'native_assembly',
                'pages':[{'id':'one','role':'photo','asset_id':'product','text_ids':[],
                    'composition':[{'role':'image','id':'subject','asset_id':'product','x':.1,'y':.1,'width':.8,'height':.6},
                                   {'role':'rule','id':'rhythm','x':.1,'y':.8,'width':.8,'height':.02}]}]}]}
    source={'run_id':'source','title':'让生活接班','blocks':[{'text':'电脑收工，给生活让一点位置'}],
        'images':[{'asset_id':'product'}],'assets':[{'id':'product','visual_role':'product','prepared_subject':True}]}
    plan=asyncio.run(plan_design({'product':'实际产品','platform':'moments','scene':'下班收尾'},source,
        instruction='围绕正文定制配图，无叠字',llm=Model()))
    assert len(calls)==1 and plan.provenance['fallback_used'] is False
    assert plan.concepts[0].production_method=='native_assembly'
    assert plan.provenance['content_visual_commission']['sha256']==calls[0]['content_visual_commission']['sha256']


def test_visual_strategy_has_equal_product_and_narrative_routes_without_forced_generation():
    from app.design.native_visual import visual_strategy_contract
    contract = visual_strategy_contract()
    assert set(contract['routes']) == {'product_hero','ksp_explanation','verbal_visual_turn','emotional_campaign','editorial_narrative'}
    assert contract['native_only_valid'] is True
    assert contract['generated_elements'] == 'optional_only_when_they_have_an_unmet_visible_job'
    assert '没有默认生活化路线' in contract['selection_rule']
    assert 'Xiaomi' not in json.dumps(contract) and '小米' not in json.dumps(contract,ensure_ascii=False)
    assert native_visual_contract({'platform':'moments'})['visual_strategy'] == contract
    assert commission()[2]['visual_strategy'] == contract


def test_production_method_wire_preserves_legacy_auto_but_rejects_unknown_modes():
    from app.design.models import DesignConcept
    from pydantic import ValidationError
    row={'id':'one','name':'准确产品主视觉','visual_thesis':'已有多角度产品与留白形成视觉重心',
         'composition':[{'id':'product','role':'image','asset_id':'real','x':.1,'y':.1,'width':.8,'height':.6}]}
    assert DesignConcept.model_validate(row).production_method == 'auto'
    for mode in ('native_assembly','generated_editorial','source_photo'):
        assert DesignConcept.model_validate({**row,'production_method':mode}).model_dump()['production_method'] == mode
    with pytest.raises(ValidationError):
        DesignConcept.model_validate({**row,'production_method':'redraw_product'})
