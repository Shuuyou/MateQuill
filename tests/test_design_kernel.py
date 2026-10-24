import asyncio
import json
import pytest
from app.design import plan_design,compose_design,get_platform_connector,render_design_svg
from app.design.models import DesignConcept,LayoutRegion
from app.design.typography import semantic_wrap,measure,fit_text

RECORD={'id':'run-1','status':'ready','title':'把想法写成下一页','intro':'带上熟悉的内容，来店体验。','blocks':[{'text':'在电脑上打开自己的文档，按平时的习惯试一遍。','fact_ids':['F1']}],
 'facts':[{'id':'F1','value':'256 GB','qualifiers':['可用容量随配置变化'],'source_id':'S1'}], 'cta':'带上自己的文档来店试试。',
 'images':[{'asset_id':'photo-1'}]}

def plan(platform='小红书',record=RECORD,llm=None):
    return asyncio.run(plan_design({'product':'HUAWEI MateBook 14','publisher_kind':'store'},record,platform=platform,llm=llm))

def test_platform_surfaces_reflow_not_just_crop():
    assert get_platform_connector('B站').width>get_platform_connector('B站').height
    assert get_platform_connector('抖音','image_note').height==1440
    assert get_platform_connector('朋友圈').suggested_pages==1
    assert get_platform_connector('微信公众号','article_body').width==1440
    with pytest.raises(ValueError):get_platform_connector('not-a-platform')

def test_photographic_moments_have_no_forced_title_or_cta():
    p=plan('朋友圈');boards=compose_design({},RECORD,p,{'photo-1':{'visual_role':'store_photo'}})
    assert len(boards)==1
    assert [l.type for l in boards[0].document.layers]==['image']
    assert boards[0].document.layers[0].asset_id=='photo-1'

def test_editable_source_copy_and_factual_qualifiers_preserved():
    p=plan();assert p.brief.texts['fact-0']['text']=='256 GB；可用容量随配置变化'
    boards=compose_design({},RECORD,p,{'photo-1':{'visual_role':'product'}})
    assert len(boards)==3
    assert len({b.concept_id for b in boards})==1
    assert len({b.document.canvas.background for b in boards})==1
    assert len({tuple((l.x,l.y,l.width,l.height) for l in b.document.layers) for b in boards})==3
    for b in boards:
        assert not any(l.role in ('watermark','missing_product') for l in b.document.layers)
        for l in b.document.layers:
            assert l.x+l.width<=b.document.width+.1
            assert l.y+l.height<=b.document.height+.1
    assert any(l.text.replace('\n','')==RECORD['title'] for l in boards[0].document.layers)

def test_missing_image_never_creates_grey_placeholder():
    record={**RECORD,'images':[]}
    boards=compose_design({},record,plan(record=record),{})
    assert all(not any(l.type=='image' for l in b.document.layers) for b in boards)
    assert any(l.type=='text' for l in boards[0].document.layers)
    assert any(i['category']=='asset' for i in boards[0].document.audit)

def test_model_cannot_introduce_unapproved_copy_or_assets():
    initial=plan().model_dump()
    class LLM:
        enabled=True
        async def _json(self,*args,**kwargs):
            data=initial
            data['concepts'][0]['text_ids']=['new-price-999']
            return {'concepts':data['concepts'],'selected_concept_id':data['selected_concept_id']}
    p=plan(llm=LLM());assert p.provenance['planner']=='model_art_direction'
    assert p.provenance['model_attempts'][0]['discarded_invalid_concepts'][0]['concept_id']==initial['concepts'][0]['id']
    assert all(set(c.text_ids)<=set(p.brief.texts) for c in p.concepts)

def test_arbitrary_valid_regions_from_model_are_retained():
    initial=plan()
    class LLM:
        async def _json(self,*args,**kwargs):
            cs=[c.model_dump() for c in initial.concepts]
            cs[0]['composition']=[{'role':'headline','x':.12,'y':.12,'width':.75,'height':.21,'align':'right'},
                {'role':'image','x':.22,'y':.41,'width':.65,'height':.50,'align':'left'}]
            return {'concepts':cs,'selected_concept_id':cs[0]['id']}
    p=plan(llm=LLM());assert p.provenance['planner']=='model_art_direction'
    assert p.concepts[0].composition[0].align=='right'
    assert p.concepts[0].composition[1].x==.22

def test_model_layout_cannot_hide_product_under_text():
    with pytest.raises(ValueError):
        DesignConcept(id='a',name='a',visual_thesis='a',composition=[
            LayoutRegion(role='image',x=.1,y=.1,width=.8,height=.8),
            LayoutRegion(role='headline',x=.2,y=.2,width=.5,height=.3)])

def test_measured_linebreak_keeps_models_and_punctuation():
    text='带上 MateBook 14，写成下一页。'
    lines=semantic_wrap(text,240,36)
    assert ''.join(lines)==text
    assert any('MateBook 14' in l for l in lines)
    assert all(not l or l[0] not in '，。）》' for l in lines)
    assert measure('WWW',32)>measure('iii',32)
    wrapped,size,fits=fit_text(text,330,250,72)
    assert fits and size>=22
    assert ''.join(wrapped.splitlines())==text

def test_portable_svg_has_exact_font_paths_and_no_external_fetch():
    record={**RECORD,'images':[]}
    b=compose_design({},record,plan(record=record),{})[0]
    svg=render_design_svg(b.document)
    assert '<path d=' in svg
    assert 'role="text"' in svg
    assert 'http://' not in svg.replace('http://www.w3.org/2000/svg','')
    assert 'font-family' not in svg
    assert b.document.layers[0].type=='text'

def test_actual_serif_and_weight_are_distinct_font_outlines():
    from app.design.font_resolver import resolve_glyph,font_provenance
    regular=resolve_glyph('想','sans',400)
    bold=resolve_glyph('想','sans',700)
    serif=resolve_glyph('想','serif',400)
    assert len({regular.path,bold.path,serif.path})==3
    assert bold.weight==700 and serif.family=='serif'
    assert all('serif/' in path for path in font_provenance('华为','serif',600)['font_files'])
    assert font_provenance('华为','serif',600)['actual_weights']==[600]


def test_variable_advance_metrics_used_by_measurement():
    from app.design.font_resolver import resolve_glyph
    text='MateBook 华为'
    for family in ('sans','serif'):
        glyphs=[resolve_glyph(char,family,700) for char in text]
        actual=sum(g.advance*64/g.units_per_em for g in glyphs)
        assert abs(measure(text,64,font_family=family,font_weight=700)-actual)<.001


def test_photo_overlay_allowed_but_colliding_text_or_product_not_allowed():
    background=LayoutRegion(role='image',image_kind='photo',x=0,y=0,width=1,height=1)
    title=LayoutRegion(role='headline',x=.1,y=.1,width=.7,height=.2)
    concept=DesignConcept(id='full-photo',name='photo',visual_thesis='photo overlay',composition=[background,title])
    assert len(concept.composition)==2
    with pytest.raises(ValueError):
        DesignConcept(id='collision',name='bad',visual_thesis='bad',composition=[background,title,
            LayoutRegion(role='body',x=.1,y=.15,width=.7,height=.3)])
    with pytest.raises(ValueError):
        DesignConcept(id='product',name='bad',visual_thesis='bad',composition=[
            LayoutRegion(role='image',x=0,y=0,width=1,height=1),title])


def test_pillow_compatibility_loads_bundled_font_from_memory():
    from app.design.typography import font
    assert font(32).getlength('华为')>0


def test_serif_is_used_in_native_layer_and_rendered_without_synthetic_stroke():
    p=plan();p.selected_concept_id='editorial-dialogue'
    b=compose_design({},RECORD,p,{'photo-1':{'visual_role':'product'}})[0]
    headline=next(layer for layer in b.document.layers if layer.role=='headline')
    assert headline.font_family=='serif' and headline.font_weight==700
    metadata=b.typography[headline.id]
    assert metadata['actual_weights']==[700]
    assert any(path.startswith('serif/') for path in metadata['font_files'])
    svg=render_design_svg(b.document)
    assert 'stroke-linejoin=' not in svg

def test_marketing_emoji_has_real_monochrome_glyph_and_weight():
    from app.design.font_resolver import resolve_glyph
    glyph=resolve_glyph('✨','sans',800)
    assert glyph.path and glyph.family=='emoji'
    assert glyph.weight==700
    assert resolve_glyph('\ufe0f').advance==0
    assert semantic_wrap('新品✨到店体验',400,48)

def test_planned_page_assets_override_sorted_catalog_order():
    record={**RECORD,'images':[{'asset_id':aid} for aid in ('side','rear','front')]}
    p=plan(record=record)
    selected=next(c for c in p.concepts if c.id==p.selected_concept_id)
    selected.page_asset_ids=['side','front','rear']
    boards=compose_design({},record,p,{aid:{'visual_role':'product'} for aid in ('side','rear','front')})
    assert [next(layer.asset_id for layer in board.document.layers if layer.type=='image') for board in boards]==['side','front','rear']
    with pytest.raises(ValueError,match='planned_design_asset_unavailable'):
        compose_design({},record,p,{'side':{},'rear':{}})


def test_unknown_model_page_asset_is_rejected():
    initial=plan().model_dump()
    class LLM:
        async def _json(self,*args,**kwargs):
            initial['concepts'][0]['page_asset_ids']=['unauthorized-file']
            return {'concepts':initial['concepts'],'selected_concept_id':initial['selected_concept_id']}
    p=plan(llm=LLM())
    assert p.provenance['planner']=='model_art_direction'
    assert all('unauthorized-file' not in c.page_asset_ids for c in p.concepts)


def test_view_revision_keeps_dark_system_and_maps_actual_observed_pages():
    old=plan().model_dump()
    old['selected_concept_id']='editorial-dialogue'
    previous=next(c for c in old['concepts'] if c['id']=='editorial-dialogue')
    record={**RECORD,'images':[{'asset_id':aid} for aid in ('side','rear','front')],
        'asset_observations':{'side':{'view':'side','framing':'侧边'},'rear':{'view':'rear','framing':'背面'},'front':{'view':'front','framing':'屏幕正面'}}}
    proposed=[c.model_dump() for c in plan(record=record).concepts]
    payloads=[]
    class LLM:
        async def _json(self,system,payload,**kwargs):
            payloads.append((system,payload))
            cs=proposed
            # A model preference for a light background cannot override the user.
            for c in cs: c.update(background='#FFFFFF',foreground='#000000',font_role='sans')
            return {'concepts':cs,'selected_concept_id':cs[0]['id']}
    p=asyncio.run(plan_design({'product':'HUAWEI MateBook 14','previous_design':old},record,
        instruction='保留深色，第二张用正面，第三张用背面',llm=LLM()))
    selected=next(c for c in p.concepts if c.id==p.selected_concept_id)
    assert p.provenance['planner']=='model_art_direction'
    assert selected.background==previous['background']
    assert selected.foreground==previous['foreground']
    assert selected.font_role==previous['font_role']
    assert [r.model_dump() for r in selected.composition]==previous['composition']
    assert selected.page_asset_ids[1:3]==['front','rear']
    assert len(p.concepts)==1
    assert payloads[0][1]['asset_observations'][1]['view']=='rear'
    assert '不能按素材排序或文件名猜' in payloads[0][0]
    boards=compose_design({},record,p,{aid:{} for aid in ('side','rear','front')})
    assert [next(l.asset_id for l in b.document.layers if l.type=='image') for b in boards][1:3]==['front','rear']


def test_view_fallback_does_not_guess_from_misleading_filename():
    record={**RECORD,'images':[{'asset_id':'front.jpg'},{'asset_id':'rear.jpg'}],
        'asset_observations':{'front.jpg':{'view':'unknown'},'rear.jpg':{'view':'unknown'}}}
    from app.design.planner import DesignInputNeeded
    with pytest.raises(DesignInputNeeded,match='正面照片'):
        asyncio.run(plan_design({},record,instruction='第二张用正面，第三张用背面'))


def test_explicit_palette_change_unlocks_previous_theme():
    from app.design.planner import _revision_constraints
    old=plan().model_dump();old['selected_concept_id']='editorial-dialogue'
    constraints=_revision_constraints({'previous_design':old},'背景改为浅色，字体换成黑体')
    assert 'background' not in constraints['locked_fields']
    assert 'font_role' not in constraints['locked_fields']

def test_authored_inner_page_is_not_replaced_by_generic_layout():
    from app.design.models import DesignPage
    record={**RECORD,'creative_plan':{'cards':[{'headline':'看这一面','overlay_text':'换个角度看细节','fact_ids':['F1']} ]}}
    p=plan(record=record)
    c=next(c for c in p.concepts if c.id==p.selected_concept_id)
    c.pages=[DesignPage(id='first',role='cover',composition=c.composition,text_ids=['title'],asset_id='photo-1'),
        DesignPage(id='second',role='detail',visual_brief='留白与设备轮廓共同形成对角阅读',asset_id='photo-1',
            text_ids=['card-0-overlay_text'],composition=[
                LayoutRegion(role='headline',text_id='card-0-overlay_text',x=.08,y=.14,width=.80,height=.14,font_scale=.078,font_weight=500),
                LayoutRegion(role='image',x=.38,y=.42,width=.55,height=.48)])]
    boards=compose_design({},record,p,{'photo-1':{}})
    image=next(layer for layer in boards[1].document.layers if layer.type=='image')
    headline=next(layer for layer in boards[1].document.layers if layer.role=='headline')
    assert image.x==.38*1080 and image.y==.42*1440
    assert headline.font_weight==500
    assert headline.text.replace('\n','')=='换个角度看细节'
    assert not any(RECORD['blocks'][0]['text'] in layer.text for layer in boards[1].document.layers)
    assert boards[1].visual_brief=='留白与设备轮廓共同形成对角阅读'


def test_full_visual_replaces_entire_native_composition_without_extra_text():
    from app.design.compositor import apply_full_frame_visual
    p=plan();board=compose_design({},RECORD,p,{'photo-1':{}})[0]
    assert any(layer.type=='text' for layer in board.document.layers)
    visual=apply_full_frame_visual(board.document,'generated-final',{'data_url':'data:image/png;base64,xxx','provenance':'reference_guided_design'})
    assert len(visual.layers)==1
    layer=visual.layers[0]
    assert layer.type=='image' and layer.role=='generated_design'
    assert (layer.x,layer.y,layer.width,layer.height)==(0,0,visual.width,visual.height)
    assert any(layer.type=='text' for layer in board.document.layers), 'prior editable source must remain intact'
    assert set(visual.assets)=={'generated-final'}


def test_short_card_copy_keeps_claim_binding_and_qualifiers():
    record={**RECORD,'creative_plan':{'cards':[{'headline':'轻装出发','overlay_text':'256 GB','fact_ids':['F1'],'disclosures':['可用容量随配置变化']}]}}
    p=plan(record=record)
    copy=p.brief.texts['card-0-overlay_text']
    assert copy['text']=='256 GB；可用容量随配置变化'
    assert copy['fact_ids']==['F1']


def test_visual_first_page_retains_intended_copy_without_native_text_requirement():
    from app.design.models import DesignPage
    p=plan();c=next(c for c in p.concepts if c.id==p.selected_concept_id)
    c.pages=[DesignPage(id='visual',role='cover',render_mode='visual_first',text_ids=['title'],asset_id='photo-1',
        composition=[LayoutRegion(role='image',x=.05,y=.05,width=.90,height=.90)],visual_brief='完整海报，标题与产品共同构图')]
    board=compose_design({},RECORD,p,{'photo-1':{}})[0]
    assert not any(layer.type=='text' for layer in board.document.layers)
    assert board.source_text_ids==['title']
    assert board.render_mode=='visual_first'


def test_unapproved_per_page_copy_cannot_enter_design_plan():
    from app.design.models import DesignPage
    initial=plan().model_dump()
    initial['concepts'][0]['pages'][0]['text_ids']=['invented-battery-claim']
    class LLM:
        async def _json(self,*args,**kwargs):return {'concepts':initial['concepts'],'selected_concept_id':initial['selected_concept_id']}
    p=plan(llm=LLM())
    assert p.provenance['planner']=='model_art_direction'
    assert all('invented-battery-claim' not in page.text_ids for c in p.concepts for page in c.pages)

def test_compact_prose_without_geometry_is_explicit_local_fallback():
    class LLM:
        calls=0
        async def _json(self,system,payload,**kwargs):
            self.calls+=1
            assert 'composition' in payload['output_contract']['page']
            return {'concepts':[{'id':'a','name':'a','visual_thesis':'A prose description',
                'pages':[{'asset_id':'photo-1','text_ids':['title'],'visual_brief':'Photography only'}]}]}
    llm=LLM();p=plan(llm=llm)
    assert p.provenance['planner']=='local_composition'
    assert p.provenance['model_attempts'][0]['validation_errors'][0]['message']=='missing_native_page_composition'
    assert llm.calls==2
    assert all(page.render_mode=='native' for c in p.concepts for page in c.pages)


NATIVE_REGIONS=[{'role':'headline','text_id':'title','x':.08,'y':.08,'width':.84,'height':.20},
    {'role':'image','x':.12,'y':.35,'width':.76,'height':.58}]


def test_schema_error_gets_one_bounded_specific_repair():
    class LLM:
        max_calls=2
        _calls=0
        async def _json(self,system,payload,**kwargs):
            self._calls+=1
            if self._calls==1:
                return {'concepts':[{'id':'a','name':'a','visual_thesis':'a','pages':[{'asset_id':'photo-1','text_ids':['title'],'composition':[{'role':'image','x':0,'y':0,'width':2,'height':1}]}]}]}
            assert payload['repair_request']['errors'][0]['loc']
            return {'concepts':[{'id':'a','name':'a','visual_thesis':'a','pages':[{'asset_id':'photo-1','text_ids':['title'],'composition':NATIVE_REGIONS,'visual_brief':'完整主体与字体相互呼应'}]}]}
    llm=LLM();p=plan(llm=llm)
    assert p.provenance['planner']=='model_art_direction'
    assert llm._calls==2
    assert p.provenance['model_attempts'][0]['outcome']=='ValidationError'
    assert p.provenance['model_attempts'][1]['outcome']=='success'


def test_one_click_prefers_usable_whole_device_and_compatible_copy():
    record={**RECORD,'images':[{'asset_id':a} for a in ('side','rear-low','rear-high')],
        'asset_observations':{'side':{'view':'side'},'rear-low':{'view':'rear'},'rear-high':{'view':'rear'}},
        'assets':[{'id':'side','effective_width':100,'effective_height':2000},
            {'id':'rear-low','effective_width':100,'effective_height':160},
            {'id':'rear-high','effective_width':1000,'effective_height':1600}],
        'creative_plan':{'cards':[{'headline':'先看侧面'}]}}
    p=plan(record=record)
    for concept in p.concepts:
        assert concept.page_asset_ids[0]=='rear-high'
        assert concept.pages[0].asset_id=='rear-high'
        assert 'card-0-headline' not in concept.pages[0].text_ids


def test_explicit_rear_main_cannot_keep_side_copy_from_model():
    record={**RECORD,'images':[{'asset_id':a} for a in ('side','rear')],
        'asset_observations':{'side':{'view':'side'},'rear':{'view':'rear'}},
        'creative_plan':{'cards':[{'headline':'先看侧面'}]}}
    class LLM:
        async def _json(self,*args,**kwargs):return {'concepts':[{'id':'a','name':'a','visual_thesis':'完整作品','pages':[{'asset_id':'side','text_ids':['card-0-headline'],'composition':NATIVE_REGIONS,'visual_brief':'产品完整主视觉'}]}]}
    p=asyncio.run(plan_design({},record,instruction='以背面完整整机为主视觉，侧视图仅辅助细节。',llm=LLM()))
    assert p.provenance['planner']=='model_art_direction'
    assert p.concepts[0].page_asset_ids[0]=='rear'
    assert p.concepts[0].pages[0].text_ids==['title']


def test_explicit_side_detail_overrides_default_whole_device_preference():
    record={**RECORD,'images':[{'asset_id':a} for a in ('side','rear')],
        'asset_observations':{'side':{'view':'side'},'rear':{'view':'rear'}},
        'creative_plan':{'cards':[{'headline':'先看侧面'}]}}
    p=asyncio.run(plan_design({},record,instruction='做一张侧面细节特写'))
    assert all(c.page_asset_ids[0]=='side' for c in p.concepts)
    assert all(c.pages[0].text_ids==['card-0-headline'] for c in p.concepts)

def test_whole_device_thumbnail_does_not_outrank_usable_front():
    record={**RECORD,'images':[{'asset_id':'rear-tiny'},{'asset_id':'front-large'}],
        'asset_observations':{'rear-tiny':{'view':'rear'},'front-large':{'view':'front'}},
        'assets':[{'id':'rear-tiny','effective_width':60,'effective_height':90},
            {'id':'front-large','effective_width':900,'effective_height':1500}]}
    p=plan(record=record)
    assert all(c.page_asset_ids[0]=='front-large' for c in p.concepts)

def test_pose_incompatible_art_direction_is_discarded_not_just_relabelled():
    record={**RECORD,'images':[{'asset_id':a} for a in ('side','rear')],
        'asset_observations':{'side':{'view':'side'},'rear':{'view':'rear'}}}
    class LLM:
        async def _json(self,*args,**kwargs):return {'selected_concept_id':'side-story','concepts':[
            {'id':'side-story','name':'侧面剖面','visual_thesis':'侧面成为主角','pages':[{'asset_id':'side','text_ids':['title'],'composition':NATIVE_REGIONS,'visual_brief':'手机背面完整整机平放，从摄像头观察细节'}]},
            {'id':'rear-story','name':'整机观察','visual_thesis':'背面与标题呼应','pages':[{'asset_id':'rear','text_ids':['title'],'composition':[{**r,'x':.10} for r in NATIVE_REGIONS],'visual_brief':'手机背面完整整机平放在有自然阴影的纸面'}]}]}
    p=asyncio.run(plan_design({},record,platform='小红书',llm=LLM()))
    assert p.provenance['planner']=='model_art_direction'
    assert p.selected_concept_id=='rear-story'
    assert p.provenance['model_attempts'][0]['discarded_conflicting_concepts']==['side-story']


def test_art_direction_cannot_smuggle_rejected_side_subtitle():
    record={**RECORD,'images':[{'asset_id':'rear'}], 'asset_observations':{'rear':{'view':'rear'}},
        'creative_plan':{'cards':[{'headline':'先看侧面'}]}}
    class LLM:
        async def _json(self,*args,**kwargs):return {'concepts':[{'id':'a','name':'a','visual_thesis':'完整整机',
            'pages':[{'asset_id':'rear','text_ids':['title'],'composition':NATIVE_REGIONS,'visual_brief':'手机背面平放于台面，副题“先看侧面”小号竖排'}]}]}
    p=asyncio.run(plan_design({},record,llm=LLM()))
    assert p.provenance['planner']=='model_art_direction'
    assert '先看侧面' not in p.concepts[0].pages[0].visual_brief


def test_native_model_geometry_vectors_and_original_asset_survive_compile():
    import base64
    import hashlib
    import io
    from PIL import Image
    stream=io.BytesIO();Image.new('RGB',(48,64),'green').save(stream,'PNG')
    raw=stream.getvalue();digest=hashlib.sha256(raw).hexdigest()
    asset={'data_url':'data:image/png;base64,'+base64.b64encode(raw).decode(),
        'sha256':digest,'source_id':'authorized-source','visual_role':'product'}
    geometry=[{**NATIVE_REGIONS[0],'font_role':'serif','font_weight':600,'letter_spacing':2},
        {**NATIVE_REGIONS[1],'asset_id':'photo-1'},
        {'role':'shape','x':.03,'y':.32,'width':.06,'height':.58,'geometry':'path',
            'path_d':'M 0 0 Q 100 50 0 100 Z','fill':'#126744','fill_style':'linear',
            'gradient_end':'#AACCEE','stroke':'#234567','stroke_width':1.5}]
    class LLM:
        calls=0
        async def _json(self,system,payload,**kwargs):
            self.calls+=1
            return {'selected_concept_id':'authored','concepts':[{'id':'authored','name':'Authored',
                'visual_thesis':'Curved observation guide',
                'pages':[{'id':'cover','asset_id':'photo-1','text_ids':['title'],'composition':geometry}]}]}
    llm=LLM();p=plan(platform='朋友圈',llm=llm)
    assert llm.calls==1 and p.provenance['planner']=='model_art_direction'
    board=compose_design({},RECORD,p,{'photo-1':asset})[0]
    assert board.render_mode=='native'
    shape=next(layer for layer in board.document.layers if layer.type=='shape')
    assert shape.path_d==geometry[-1]['path_d'] and shape.fill_style=='linear'
    assert shape.gradient_end=='#AACCEE' and shape.stroke_width==1.5
    title=next(layer for layer in board.document.layers if layer.type=='text')
    assert title.font_family=='serif' and title.letter_spacing==2
    assert board.document.assets['photo-1']==asset
    assert hashlib.sha256(base64.b64decode(board.document.assets['photo-1']['data_url'].split(',')[1])).hexdigest()==digest
    svg=render_design_svg(board.document)
    assert '<linearGradient' in svg and 'M 0 0 Q 100 50 0 100 Z' in svg
    assert asset['data_url'] in svg


@pytest.mark.parametrize('changes',[
    {'path_d':'M0 0 <script>alert(1)</script>'},
    {'path_d':'M'+'1 '*2501},
    {'gradient_end':'url(https://invalid.example)'},
    {'stroke_width':float('nan')},
    {'fill_style':'url'},
])
def test_native_shape_rejects_unsafe_or_unbounded_values(changes):
    with pytest.raises(ValueError):
        LayoutRegion(role='shape',x=0,y=0,width=1,height=1,**changes)


def test_legacy_visual_first_saved_plan_still_parses():
    from app.design.models import DesignPlan
    saved=plan().model_dump()
    saved['concepts'][0]['pages'][0]['render_mode']='visual_first'
    restored=DesignPlan.model_validate(saved)
    assert restored.concepts[0].pages[0].render_mode=='visual_first'


def test_new_visual_first_proposal_cannot_claim_native_art_direction():
    class LLM:
        async def _json(self,*args,**kwargs):
            return {'concepts':[{'id':'old','name':'old','visual_thesis':'Old raster request',
                'pages':[{'asset_id':'photo-1','render_mode':'visual_first',
                    'text_ids':['title'],'composition':NATIVE_REGIONS}]}]}
    p=plan(llm=LLM())
    assert p.provenance['planner']=='local_composition'
    assert p.provenance['model_attempts'][0]['validation_errors'][0]['message']=='new_proposal_requires_native_composition'
