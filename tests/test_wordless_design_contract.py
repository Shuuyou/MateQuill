"""Explicit caller no-overlay requests survive real native planning/rendering."""
import asyncio
import json
from uuid import uuid4

from fastapi import HTTPException
import pytest

from app.design.publication import no_overlay_text_requested
from app.design_copy import prepare_design_copy
from app import design_service as service
from app.tenancy import load_body
from test_design_context_integration import pipeline
from test_generation_jobs_durability import identities, acting
from test_design_hook_binding import document


@pytest.mark.parametrize('instruction,expected', [
    ('无叠字、无标题、无参数和按钮；只用真实产品照片',True),
    ('不要文字，只做原生取景',True), ('无字',True), ('请制作无字配图即可',True),
    ('不要改成纯照片，保留标题',False), ('不要做无字图，保留标题',False),
    ('无标题，但保留短caption',False), ('标题写“无字天书”',False),
    ('图题：无字天书',False), ('图题写「纯照片」，不是处理指令',False),
])
def test_only_explicit_no_overlay_request_controls_policy(instruction,expected):
    assert no_overlay_text_requested(instruction) is expected


def test_no_overlay_copy_is_empty_without_claiming_a_model_review_or_calling_a_writer():
    class Unexpected:
        async def _json(self,*args,**kwargs):
            pytest.fail('No wording is requested; do not pay to invent and audit a headline')
    result=asyncio.run(prepare_design_copy({}, {}, brand_identity={}, store_context={},
        llm=Unexpected(),auditor=Unexpected(),instruction='无叠字，只用原图'))
    assert result['status']=='approved' and result['cards']==[]
    assert result['audit']['model_review_performed'] is False
    assert result['audit']['scope']=='explicit_request_no_overlay_copy'


def test_model_or_saved_copy_cannot_self_declare_wordless_to_remove_a_required_headline():
    prepared={'status':'approved','audit':{'completed':True},'cards':[{'headline':'原审定标题'}],
              'text_policy':'no_overlay_text'}
    with pytest.raises(HTTPException) as error:
        service._validate_delivered_copy(document('品牌名字','品牌名字'),prepared)
    assert error.value.detail['code']=='approved_design_hook_missing'
    with pytest.raises(HTTPException) as error:
        service._validate_delivered_copy(document('新增标题','品牌名字'),prepared,instruction='无叠字')
    assert error.value.detail['code']=='unexpected_overlay_text_in_wordless_design'


def test_wordless_planner_rejects_even_an_unbound_automatic_brand_text_region():
    from app.design.planner import _candidate_sources
    from app.design.models import DesignBrief,DesignConcept,DesignPage,LayoutRegion
    from app.design.connectors import get_platform_connector
    regions=[LayoutRegion(role='image',asset_id='real',x=0,y=0,width=1,height=.8),
             LayoutRegion(role='brand',x=.1,y=.85,width=.8,height=.1)]
    candidate=DesignConcept(id='bad',name='模型想加字',visual_thesis='模型提出额外品牌排版',composition=regions,
        pages=[DesignPage(id='p',role='photo',asset_id='real',composition=regions)])
    brief=DesignBrief(platform=get_platform_connector('moments'),instruction='无叠字，只用照片')
    with pytest.raises(ValueError,match='unexpected_overlay_text_in_wordless_design'):
        _candidate_sources(candidate,brief,['real'])


@pytest.mark.real_tenancy
def test_explicit_no_overlay_through_actual_service_plan_native_render_and_source_validation(pipeline):
    p=pipeline
    calls=[]
    async def wordless(system,payload,**kwargs):
        calls.append(payload)
        assert 'brief' in payload, 'No copy writer/auditor belongs to this text-free task'
        assert payload['brief']['text_policy']=='no_overlay_text'
        assert payload['brief']['approved_copy']=={}
        return {'selected_concept_id':'photo-only','concepts':[{
            'id':'photo-only','name':'原生取景','visual_thesis':'原始产品与留白形成观看关系',
            'treatment':'photo','pages':[{'id':'photo','role':'photo','asset_id':'real-product',
                'text_ids':[],'composition':[{'role':'image','asset_id':'real-product','image_kind':'product',
                    'x':.05,'y':.05,'width':.9,'height':.9}]}]}]}
    p.model._json=wordless
    identifier=uuid4().hex
    with acting(p.owner):
        result=asyncio.run(service.execute_design({'design_id':identifier,'source_run_id':p.run_id,
            'platform':'moments','instruction':'无叠字、无标题、无参数和按钮；只用真实产品照片'}))
        saved=load_body('design',json.loads(result.body)['id'])
        work=load_body('design_work',identifier)
    assert saved['status']=='ready'
    assert len(calls)==1
    assert work['design_copy']['cards']==[]
    assert work['design_copy']['audit']['model_review_performed'] is False
    assert saved['plan']['provenance']['text_policy']=='no_overlay_text'
    assert saved['plan']['brief']['texts']=={}
    board=saved['artboards'][0]
    assert board['review']['review_completed'] is True
    assert board['source_integrity']['source_bytes_unchanged'] is True
    assert board['render_sha256']
    assert not any(layer['type']=='text' and layer.get('visible',True) for layer in board['document']['layers'])
