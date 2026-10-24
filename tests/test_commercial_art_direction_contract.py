"""Native art direction is bounded, source-bound and actually composited."""
import asyncio

import pytest

from app.design.compositor import compose_design
from app.design.models import DesignConcept, DesignPage, LayoutRegion
from app.design.planner import plan_design
from app.design.planner import source_texts


def region(role, **kwargs):
    return LayoutRegion(role=role, x=.1, y=.1, width=.7, height=.7, **kwargs)


def concept(regions):
    return DesignConcept(id='material-light', name='材质与光', visual_thesis='真实产品与光带形成同一主视觉', composition=regions)


def test_intentional_product_array_is_allowed_without_repainting_assets():
    c = concept([region('image', asset_id='rear', z=10),
                 region('image', asset_id='front', z=12, overlap_intent='layered_assets')])
    assert [r.asset_id for r in c.composition] == ['rear', 'front']
    with pytest.raises(ValueError, match='overlapping_content_regions'):
        concept([region('image', asset_id='rear'), region('image', asset_id='front')])


def test_type_behind_subject_does_not_license_type_on_top_of_product():
    assert concept([region('headline', z=5, overlap_intent='type_behind_subject'), region('image', z=10)])
    with pytest.raises(ValueError, match='overlapping_content_regions'):
        concept([region('headline', z=15, overlap_intent='type_behind_subject'), region('image', z=10)])
    # Actual z order is sufficient intent; do not require a second redundant
    # label before accepting a legal title-behind-subject composition.
    assert concept([region('headline', z=5), region('image', z=10)])
    assert concept([region('image', z=5), region('image', z=10)])


@pytest.mark.parametrize('role',['caption','body','brand','kicker'])
def test_functional_copy_cannot_be_hidden_behind_a_product(role):
    with pytest.raises(ValueError,match='functional_text_occlusion_requires_replan'):
        concept([region(role,z=5,text_id='approved-copy'),region('image',z=10)])
    with pytest.raises(ValueError,match='artistic_headline_only'):
        region(role,z=5,overlap_intent='type_behind_subject')


def test_full_bleed_photo_allows_readable_type_above_but_not_hidden_type_below():
    assert concept([region('image',image_kind='photo',z=10),region('caption',z=20)])
    for role in ('headline','caption','brand'):
        with pytest.raises(ValueError,match='occlusion_requires_replan'):
            concept([region('image',image_kind='photo',z=10),region(role,z=5)])


def test_region_contract_is_bounded_and_has_one_canonical_role_vocabulary():
    for kwargs in ({'z':41}, {'z':-21}, {'overlap_intent':'freeform'}, {'rotation':181},
                   {'shadow_color':'red'}, {'shadow_opacity':.61}, {'shadow_blur':81}, {'shadow_dx':201}):
        with pytest.raises(ValueError): region('image', **kwargs)
    with pytest.raises(ValueError): region('hero')


def test_page_and_concept_share_twenty_region_limit():
    rows = [region('shape') for _ in range(20)]
    assert len(concept(rows).composition) == 20
    assert len(DesignPage(id='cover', composition=rows).composition) == 20
    for constructor in (concept, lambda r: DesignPage(id='cover', composition=r)):
        with pytest.raises(ValueError): constructor(rows + [region('shape')])


def model_proposal():
    # Fourteen regions was previously accepted on the page but rejected when
    # lifted to its concept, silently replacing a real proposal with fallback.
    rows = [{'role':'shape', 'x':0, 'y':0, 'width':1, 'height':1,
             'opacity':.04, 'z':i-10, 'fill':'#114455'} for i in range(12)]
    rows += [{'role':'headline', 'text_id':'title', 'x':.08, 'y':.08, 'width':.84, 'height':.16},
             {'role':'image', 'asset_id':'real', 'x':.08, 'y':.30, 'width':.84, 'height':.60,
              'rotation':-5,'shadow_opacity':.2,'shadow_blur':12,'shadow_dy':9}]
    return {'selected_concept_id':'material-light', 'concepts':[{
        'id':'material-light', 'name':'材质与光', 'visual_thesis':'从取景任务提取明暗层次',
        'art_direction':{'key_visual':'真实机身与光带', 'typography':'短标题与镜头尺度呼应',
                         'material_and_light':'从机身青色延伸冷色光', 'spatial_hierarchy':'投影在机身下',
                         'local_relevance':'夜间步行的观察任务'},
        'pages':[{'id':'cover', 'asset_id':'real', 'text_ids':['title'], 'composition':rows}]}]}


def test_model_fourteen_region_art_direction_survives_and_compiles_real_z():
    class Model:
        async def _json(self, system, payload, **kwargs):
            assert payload['region_contract']['max_regions_per_concept'] == 20
            assert 'layered_assets' in payload['region_contract']['overlap_intent']
            assert '华为' in system and '现成官网KV' in system
            assert payload['asset_catalog'][0]['prepared_subject'] is True
            assert 'data_url' not in payload['asset_catalog'][0]
            return model_proposal()

    task = {'product':'HUAWEI Mate 80', 'scene':'夜间步行观察招牌字'}
    record = {'id':'source-run', 'title':'留意那一处光', 'images':[{'asset_id':'real'}],
              'assets':[{'id':'real','asset_kind':'packshot','prepared_subject':True,'alpha_fraction':.5,
                         'data_url':'private-pixels','url':'private-url'}]}
    plan = asyncio.run(plan_design(task, record, platform='朋友圈', llm=Model()))
    assert plan.provenance['planner'] == 'model_art_direction'
    assert plan.provenance['fallback_used'] is False
    assert plan.provenance['commercial_quality'] == 'pending_actual_render_review'
    assert len(plan.concepts[0].composition) == 14
    assert plan.concepts[0].art_direction.material_and_light == '从机身青色延伸冷色光'
    board = compose_design(task, record, plan, {'real':{'visual_role':'product', 'sha256':'source-bytes'}})[0]
    product = next(l for l in board.document.layers if l.type == 'image')
    shapes = [l for l in board.document.layers if l.type == 'shape']
    assert len(shapes) == 12
    assert [l.z for l in shapes] == list(range(-10,2))
    assert product.z == 10
    assert product.rotation == -5
    assert (product.shadow_opacity, product.shadow_blur, product.shadow_dy) == (.2, 12, 9)
    assert board.document.assets['real']['sha256'] == 'source-bytes'
    assert all(l.z < product.z for l in shapes)
    assert board.document.status == 'draft'  # No unit test certifies final pixels.


def test_failed_model_keeps_failure_provenance_not_a_commercial_pass():
    class BrokenModel:
        async def _json(self, *args, **kwargs):
            return {'concepts':[{'id':'bad', 'pages':[]}]}
    plan = asyncio.run(plan_design({'product':'Mate 80'}, {'title':'看见细节', 'images':[{'asset_id':'real'}]}, llm=BrokenModel()))
    assert plan.provenance['fallback_used'] is True
    assert plan.provenance['fallback_reason']
    assert len(plan.provenance['model_attempts']) == 2
    assert plan.provenance['commercial_quality'] == 'pending_actual_render_review'


def test_prepared_subject_preferred_without_turning_automatic_choice_into_user_assignment():
    record={'id':'run','title':'换个角度看','images':[{'asset_id':'old-kv'},{'asset_id':'prepared'}],
        'assets':[{'id':'old-kv','asset_kind':'official_kv','width':2000,'height':2000},
                  {'id':'prepared','asset_kind':'packshot','prepared_subject':True,
                   'effective_width':900,'effective_height':1200,'alpha_fraction':.5}],
        'asset_observations':{'old-kv':{'view':'rear'},'prepared':{'view':'front'}}}
    class Artist:
        async def _json(self,system,payload,**kwargs):
            assert payload['preferred_primary_asset_id']=='prepared'
            assert payload['explicit_page_assignments']=={}
            data=model_proposal()
            page=data['concepts'][0]['pages'][0]
            page['asset_id']='prepared'
            page['composition'][-1]['asset_id']='prepared'
            return data
    plan=asyncio.run(plan_design({'product':'Mate 80'},record,platform='朋友圈',llm=Artist()))
    assert plan.provenance['planner']=='model_art_direction'
    assert plan.provenance['explicit_page_assignments']=={}
    assert plan.provenance['preferred_primary_asset_id']=='prepared'
    assert plan.concepts[0].pages[0].asset_id=='prepared'
    assert plan.concepts[0].pages[0].composition[-1].asset_id=='prepared'


def test_real_explicit_view_assignment_still_overrides_prepared_default():
    record={'title':'看见背面','images':[{'asset_id':'rear-kv'},{'asset_id':'front-subject'}],
        'assets':[{'id':'rear-kv','asset_kind':'official_kv'},
                  {'id':'front-subject','asset_kind':'packshot','prepared_subject':True}],
        'asset_observations':{'rear-kv':{'view':'rear'},'front-subject':{'view':'front'}}}
    plan=asyncio.run(plan_design({'product':'Mate 80'},record,instruction='产品背面作为主视觉',platform='朋友圈'))
    assert plan.provenance['explicit_page_assignments']=={'1':'rear-kv'}


def test_current_approved_poster_copy_is_the_only_public_typography_catalog():
    record={'title':'旧训诫标题','intro':'旧正文','cta':'旧长句邀请',
        '_brand_identity':{'product_naming':{'canonical_name':'HUAWEI Mate 80'}},
        '_design_copy':{'status':'approved','audit':{'completed':True},'cards':[
            {'headline':'换个视角看午休','caption':'三种取景，慢慢选。'}]}}
    texts=source_texts(record)
    assert set(texts)=={'identity-product-label','design-copy-0-headline','design-copy-0-caption'}
    class Artist:
        async def _json(self,system,payload,**kwargs):
            assert 'title' not in payload['brief']['approved_copy']
            assert 'cta' not in payload['brief']['approved_copy']
            assert 'output_example' not in payload
            assert 'output_contract' in payload
            data=model_proposal()
            page=data['concepts'][0]['pages'][0]
            page['text_ids']=['design-copy-0-headline']
            page['composition'][-2]['text_id']='design-copy-0-headline'
            return data
    record['images']=[{'asset_id':'real'}]
    plan=asyncio.run(plan_design({'product':'HUAWEI Mate 80'},record,platform='朋友圈',llm=Artist()))
    assert plan.provenance['planner']=='model_art_direction'
    board=compose_design({},record,plan,{'real':{'visual_role':'product'}})[0]
    visible=''.join(layer.text.replace('\n','') for layer in board.document.layers)
    assert '换个视角看午休' in visible and '旧' not in visible


def test_visual_mechanism_must_reference_real_executable_regions():
    row=region('shape',id='observation-axis')
    c=DesignConcept(id='a',name='a',visual_thesis='可见关系',composition=[row],
        visual_devices=[{'mechanism':'framing','purpose':'引导观察尺度','region_ids':['observation-axis']}])
    assert c.visual_devices[0].region_ids==['observation-axis']
    with pytest.raises(ValueError,match='unknown_visual_device_region'):
        DesignConcept(id='a',name='a',visual_thesis='空洞描述',composition=[row],
            visual_devices=[{'mechanism':'sequence','purpose':'声称有序列','region_ids':['not-rendered']}])


def test_title_reflows_real_glyphs_without_stranding_two_characters_or_rewriting_copy():
    title='一二三四五六七八九十'
    class Artist:
        async def _json(self,*args,**kwargs):
            data=model_proposal()
            rows=data['concepts'][0]['pages'][0]['composition']
            rows[-2].update(height=.30,font_scale=.09)
            rows[-1].update(y=.50,height=.45)
            return data
    record={'title':title,'images':[{'asset_id':'real'}]}
    plan=asyncio.run(plan_design({'product':'Mate 80'},record,platform='朋友圈',llm=Artist()))
    board=compose_design({},record,plan,{'real':{'visual_role':'product'}})[0]
    headline=next(layer for layer in board.document.layers if layer.role=='headline')
    assert headline.text.replace('\n','')==title
    assert '\n' not in headline.text
    assert headline.font_size<.09*board.document.width


def test_one_invalid_direction_cannot_discard_an_independently_valid_other_direction():
    class Artist:
        calls=0
        async def _json(self,system,payload,**kwargs):
            self.calls+=1
            schema=payload['schema_contract']
            assert schema['concept']['properties']['font_role']['enum']==['sans','serif','display']
            assert 'spatial_rhythm' in schema['visual_device']['properties']['mechanism']['enum']
            assert 'title' in schema['page']['properties']
            data=model_proposal()
            valid=data['concepts'][0]
            invalid={**valid,'id':'invalid-direction','font_role':'geometric-modern'}
            data['concepts']=[invalid,valid]
            data['selected_concept_id']='invalid-direction'
            return data
    artist=Artist()
    plan=asyncio.run(plan_design({'product':'Mate 80'}, {'title':'观察光','images':[{'asset_id':'real'}]},platform='朋友圈',llm=artist))
    assert artist.calls==1
    assert plan.provenance['planner']=='model_art_direction'
    assert plan.selected_concept_id=='material-light'
    dropped=plan.provenance['model_attempts'][0]['discarded_invalid_concepts']
    assert dropped[0]['concept_id']=='invalid-direction'
    assert dropped[0]['validation_errors'][0]['type']=='literal_error'


def test_partial_acceptance_never_keeps_a_direction_with_overlapping_text():
    class Artist:
        async def _json(self,*args,**kwargs):
            data=model_proposal()
            import copy
            invalid=copy.deepcopy(data['concepts'][0]);invalid['id']='text-collision'
            rows=invalid['pages'][0]['composition']
            rows.append({**rows[-2],'role':'caption'})
            data['concepts'].insert(0,invalid);data['selected_concept_id']='text-collision'
            return data
    plan=asyncio.run(plan_design({'product':'Mate 80'}, {'title':'观察光','images':[{'asset_id':'real'}]},platform='朋友圈',llm=Artist()))
    assert plan.provenance['planner']=='model_art_direction'
    assert [c.id for c in plan.concepts]==['material-light']
    dropped=plan.provenance['model_attempts'][0]['discarded_invalid_concepts']
    assert 'overlapping_content_regions' in dropped[0]['validation_errors'][0]['message']


def test_verified_multi_view_subject_preserves_reviewed_headline_and_caption():
    record={'title':'旧标题不得回填','images':[{'asset_id':'real'}],
        '_brand_identity':{'product_naming':{'canonical_name':'HUAWEI Mate 80'}},
        '_design_copy':{'status':'approved','audit':{'completed':True},'cards':[
            {'headline':'换个角度看机背','caption':'把观察留给自己'}]},
        'asset_observations':{'real':{'view':'unknown'}},
        'assets':[{'id':'real','prepared_subject':True,'asset_kind':'packshot',
                   'visible_views':['front','rear'],'visible_views_basis':'pixel_review_sha256'}]}
    class Artist:
        async def _json(self,system,payload,**kwargs):
            assert payload['asset_observations'][0]['visible_views']==['front','rear']
            data=model_proposal();page=data['concepts'][0]['pages'][0]
            page['text_ids']=['design-copy-0-headline'];page['composition'][-2]['text_id']='design-copy-0-headline'
            return data
    plan=asyncio.run(plan_design({'product':'HUAWEI Mate 80'},record,platform='朋友圈',llm=Artist()))
    assert plan.provenance['planner']=='model_art_direction'
    page=plan.concepts[0].pages[0]
    assert page.composition[-2].text_id=='design-copy-0-headline'
    assert page.text_ids==['design-copy-0-headline']
    assert 'title' not in plan.brief.texts
    board=compose_design({},record,plan,{'real':{'visual_role':'product'}})[0]
    assert '换个角度看机背' in ''.join(l.text.replace('\n','') for l in board.document.layers)


def test_reviewed_copy_view_conflict_is_rejected_not_replaced_with_identity():
    record={'images':[{'asset_id':'real'}],
        '_brand_identity':{'product_naming':{'canonical_name':'HUAWEI Mate 80'}},
        '_design_copy':{'status':'approved','audit':{'completed':True},'cards':[{'headline':'看见机背细节'}]},
        'asset_observations':{'real':{'view':'front'}}}
    class Artist:
        async def _json(self,*args,**kwargs):
            data=model_proposal();page=data['concepts'][0]['pages'][0]
            page['text_ids']=['design-copy-0-headline'];page['composition'][-2]['text_id']='design-copy-0-headline'
            return data
    plan=asyncio.run(plan_design({'product':'Mate 80'},record,platform='朋友圈',llm=Artist()))
    assert plan.provenance['fallback_used'] is True
    assert 'approved_copy_view_conflict' in plan.provenance['model_attempts'][0]['validation_errors'][0]['message']
    assert plan.brief.texts['design-copy-0-headline']['text']=='看见机背细节'
    assert any(r.text_id=='design-copy-0-headline' for c in plan.concepts for p in c.pages for r in p.composition)


def test_long_revision_and_actual_render_feedback_reach_model_with_real_typography_limits():
    instruction='x'*3100+'保留完整拍摄任务，禁止重复整组正背双机。'
    record={'title':'换个角度看午休','images':[{'asset_id':'real'}],
        '_design_feedback':[{'concept_id':'rejected','render_sha256':'a'*64,
            'checks':[{'category':'specificity','severity':'error','message':'双机重复不能表达取景变化','private_token':'must-not-copy'}],
            'actions':['保留一组真实主体，重构可见取景关系'],'private_url':'must-not-copy'}]}
    class Artist:
        async def _json(self,system,payload,**kwargs):
            assert payload['brief']['instruction']==instruction
            feedback=payload['actual_render_feedback'][0]
            assert feedback['actions']==['保留一组真实主体，重构可见取景关系']
            assert feedback['checks'][0]['message']=='双机重复不能表达取景变化'
            assert 'private_url' not in feedback and 'private_token' not in feedback['checks'][0]
            limits=payload['typography_constraints']
            assert limits['role_min_font_px']['headline']==54
            assert limits['role_min_font_px']['caption']==36
            assert limits['copy_metrics']['title']['one_line_width_at_minimum_px']>0
            assert limits['copy_metrics']['title']['minimum_font_px']==54
            assert '一组pair只出现一次' in system
            return model_proposal()
    plan=asyncio.run(plan_design({'product':'Mate 80'},record,instruction=instruction,platform='朋友圈',llm=Artist()))
    assert plan.provenance['planner']=='model_art_direction'
    assert plan.brief.instruction==instruction


def test_typography_contract_is_also_present_in_bounded_schema_repair():
    class Artist:
        calls=0
        async def _json(self,system,payload,**kwargs):
            self.calls+=1
            data=model_proposal()
            if self.calls==1:data['concepts'][0]['font_role']='bad-enum'
            else:
                assert payload['repair_request']['typography_constraints']==payload['typography_constraints']
                assert payload['repair_request']['schema_contract']==payload['schema_contract']
            return data
    artist=Artist()
    plan=asyncio.run(plan_design({'product':'Mate 80'},{'title':'观察光','images':[{'asset_id':'real'}]},platform='朋友圈',llm=artist))
    assert artist.calls==2 and plan.provenance['planner']=='model_art_direction'


def test_reference_only_kv_is_visible_as_brand_reference_but_not_composition_source():
    record={'title':'留意那一束光','images':[{'asset_id':'real'}],
        'assets':[{'id':'real','asset_kind':'packshot','prepared_subject':True},
                  {'id':'old-kv','asset_kind':'official_kv','title':'品牌视觉参考'}]}
    class Artist:
        async def _json(self,system,payload,**kwargs):
            catalog={asset['id']:asset for asset in payload['asset_catalog']}
            assert catalog['real']['usage']=='composition_source'
            assert catalog['old-kv']['usage']=='reference_only'
            assert payload['brief']['asset_ids']==['real']
            assert 'reference_only' in system
            return model_proposal()
    plan=asyncio.run(plan_design({'product':'Mate 80'},record,platform='朋友圈',llm=Artist()))
    assert plan.provenance['planner']=='model_art_direction'
    assert 'old-kv' not in plan.brief.asset_ids
