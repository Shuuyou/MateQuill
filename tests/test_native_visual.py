"""Native visual intent stays source-bound; synthetic pixels verify real rendering."""
import asyncio
import base64
from copy import deepcopy
import hashlib
import io

from PIL import Image
import pytest

from app.design.native_visual import native_visual_contract
from app.design.models import DesignBrief, DesignPlan, DesignConcept, DesignArtboard, LayoutRegion
from app.design.connectors import get_platform_connector
from app.poster import PosterBrief, PosterCanvas, PosterDocument, PosterLayer
from app import design_service


def brief(**changes):
    return {'platform': 'moments', 'publisher_kind': 'store', 'scene': '看清桌面产品接口',
            'store_context': {'photo_asset_ids': ['photo']}, **changes}


def test_unknown_photo_id_or_a_label_does_not_create_store_scene_authority():
    contract = native_visual_contract(brief(), role='photo', assets={'unrelated': {'title': '本店实拍'}})
    assert contract['authorized_photo_ids'] == []
    assert contract['selected'] == 'product_observation'
    assert contract['scene_authority'] == 'designed_scene_not_store_evidence'
    assert contract['pixel_preservation_verified'] is False


@pytest.mark.parametrize('publisher', ['store', 'brand', 'salesperson'])
def test_source_and_request_not_publisher_stereotype_determine_photo_route(publisher):
    value = native_visual_contract(brief(publisher_kind=publisher), role='photo', assets={'photo': {}})
    assert value['selected'] == 'source_observation'


def test_explicit_editorial_request_can_use_a_photo_without_becoming_fake_ugc():
    value = native_visual_contract(brief(instruction='重新设计一张品牌大片'), role='photo', assets={'photo': {}})
    assert value['selected'] == 'editorial_concept'
    assert '设计稿' in value['authenticity_scope']


@pytest.mark.parametrize('role,selected', [('detail', 'detail_annotation'), ('proof', 'choice_explanation'), ('explanation', 'choice_explanation')])
def test_explanation_pages_have_a_visible_job_not_repeated_hero(role, selected):
    assert native_visual_contract(brief(), role=role)['selected'] == selected


@pytest.fixture
def source_photo(monkeypatch):
    # This is a deterministic test image, never claimed as an actual shop photo.
    image = Image.new('RGB', (320, 320), '#3a7e92')
    for x in range(160, 320):
        for y in range(320):
            image.putpixel((x, y), (194, 128, 75))
    output = io.BytesIO(); image.save(output, 'PNG'); raw = output.getvalue()
    asset = {'source_id': 'synthetic-authorized-photo', 'sha256': hashlib.sha256(raw).hexdigest(),
             'data_url': 'data:image/png;base64,'+base64.b64encode(raw).decode(),
             'visual_role': 'store_photo', 'provenance': 'store_upload', 'prepared_subject': True}
    document = PosterDocument(width=320, height=320, canvas=PosterCanvas(width=320,height=320),
        brief=PosterBrief(width=320,height=320), assets={'photo':deepcopy(asset)},
        layers=[PosterLayer(id='photo-layer',type='image',role='photograph',locked=True,
            asset_id='photo',x=0,y=0,width=320,height=320)])
    concept = DesignConcept(id='observe',name='具体观察',visual_thesis='保留本次照片关系',treatment='photo',
        composition=[LayoutRegion(role='image',asset_id='photo',image_kind='photo',x=0,y=0,width=1,height=1)])
    plan = DesignPlan(brief=DesignBrief(platform=get_platform_connector('moments'),
        instruction='保留原图，不要换背景',store_context={'photo_asset_ids':['photo']}),
        concepts=[concept],selected_concept_id=concept.id)
    board = DesignArtboard(id='board',role='photo',title='具体观察',document=document,concept_id=concept.id)
    checkpoints=[]
    monkeypatch.setattr(design_service,'_checkpoint',lambda identifier,state:checkpoints.append(deepcopy(state)))
    # Old eligibility would consider a paid abstract plate for prepared_subject.
    monkeypatch.setattr(design_service.settings,'image_api_key','synthetic-unused')
    monkeypatch.setattr(design_service.settings,'image_model','synthetic-unused')
    return plan,board,{'photo':asset},raw,checkpoints


def test_real_entry_preserves_authorized_source_hash_and_does_not_generate_plate(source_photo,monkeypatch):
    plan,board,assets,raw,checkpoints=source_photo
    from app.image_service import ImageService
    monkeypatch.setattr(ImageService,'edit',lambda *args,**kwargs:pytest.fail('No paid image edit belongs to source-preserving photo'))
    state={}
    result=asyncio.run(design_service._design_elements_treatment('design',state,plan,board,assets))
    assert result is board.document
    trace=state['source_preservation']['board']
    assert trace['new_image_generated'] is False and trace['product_identity_verified'] is False
    assert trace['source_integrity']['source_bytes_unchanged'] is True
    assert trace['source_integrity']['placements'][0]['render_asset_sha256']==hashlib.sha256(raw).hexdigest()
    assert checkpoints
    # Recovery of the same source does not checkpoint/replay another effect.
    asyncio.run(design_service._design_elements_treatment('design',state,plan,board,assets))
    assert len(checkpoints)==1


def test_source_native_route_rejects_tampered_bytes_not_just_metadata_labels(source_photo):
    plan,board,assets,_,_=source_photo
    board.document.assets['photo']['sha256']='0'*64
    with pytest.raises(ValueError,match='source_hash_mismatch'):
        asyncio.run(design_service._design_elements_treatment('design',{},plan,board,assets))


def test_explicit_native_product_composition_skips_paid_decor_without_skipping_source_proof(source_photo,monkeypatch):
    plan,board,assets,raw,checkpoints=source_photo
    plan.concepts[0].production_method='native_assembly'
    plan.concepts[0].treatment='product'
    board.document.layers[0].role='product'
    from app.image_service import ImageService
    monkeypatch.setattr(ImageService,'generate_background',lambda *a,**k:pytest.fail('Native product design is not a background-generation request'))
    state={}
    result=asyncio.run(design_service._design_elements_treatment('design',state,plan,board,assets))
    assert result is board.document
    proof=state['native_design_composition'][board.id]
    assert proof['method']=='native_assembly' and proof['new_image_generated'] is False
    assert proof['source_integrity']['placements'][0]['render_asset_sha256']==hashlib.sha256(raw).hexdigest()
    board.document.assets['photo']['sha256']='0'*64
    with pytest.raises(ValueError,match='source_hash_mismatch'):
        asyncio.run(design_service._design_elements_treatment('design',state,plan,board,assets))


def test_source_native_route_requires_the_actual_photograph_layer(source_photo,monkeypatch):
    plan,board,assets,_,_=source_photo
    board.document.layers[0].role='product'
    monkeypatch.setattr(design_service.settings,'image_api_key','')
    state={}
    asyncio.run(design_service._design_elements_treatment('design',state,plan,board,assets))
    assert 'source_preservation' not in state


def test_actual_native_png_retains_the_source_photo_colors_and_structure(source_photo):
    plan,board,assets,_,_=source_photo
    document=asyncio.run(design_service._design_elements_treatment('design',{},plan,board,assets))
    raw=asyncio.run(design_service.render_document(document))
    with Image.open(io.BytesIO(raw)) as result:
        assert result.size==(320,320)
        assert result.convert('RGB').getpixel((80,160))==(58,126,146)
        assert result.convert('RGB').getpixel((240,160))==(194,128,75)


def test_actual_planner_receives_and_persists_native_direction_without_an_extra_call():
    from app.design.planner import plan_design
    from test_commercial_art_direction_contract import model_proposal
    calls=[]
    class Model:
        async def _json(self, system, payload, **kwargs):
            calls.append(payload)
            assert payload['native_visual_contract']['selected']=='product_observation'
            assert '不把所有内容做成品牌KV' in system
            return model_proposal()
    plan=asyncio.run(plan_design({'product':'Mate 80','scene':'观察真实接口','platform':'moments'},
        {'title':'先看一眼','images':[{'asset_id':'real'}],
         'assets':[{'id':'real','visual_role':'product','asset_kind':'packshot'}]},llm=Model()))
    assert len(calls)==1
    assert plan.provenance['native_visual_contract']['selected']=='product_observation'
    assert plan.provenance['native_visual_contract']['pixel_preservation_verified'] is False


def test_wordless_single_source_canvas_records_native_ratio_and_preserves_asset_metadata(source_photo):
    from app.design.native_visual import apply_native_photo_canvas
    plan,board,assets,raw,_=source_photo
    plan.concepts[0].pages=[]
    from app.design.models import DesignPage
    plan.concepts[0].pages=[DesignPage(id='p',asset_id='photo',role='photo',composition=[
        LayoutRegion(role='image',asset_id='photo',x=.1,y=.2,width=.8,height=.6)])]
    plan.brief.instruction='无叠字，只用原图'
    assets['photo'].update(width=1600,height=900)
    before=deepcopy(assets)
    trace=apply_native_photo_canvas(plan.brief,plan.concepts[0],assets)
    assert trace['canvas']==[1080,608]
    assert trace['source_pixels_changed'] is False and assets==before
    region=plan.concepts[0].pages[0].composition[0]
    assert (region.x,region.y,region.width,region.height)==(0,0,1,1)


@pytest.mark.parametrize('change', ['fixed_square','information_role','unknown_dimensions','not_wordless'])
def test_source_aspect_default_does_not_override_explicit_format_or_information_layout(source_photo,change):
    from app.design.native_visual import apply_native_photo_canvas
    from app.design.models import DesignPage
    plan,_,assets,_,_=source_photo
    plan.brief.instruction='无叠字，只用原图'
    plan.concepts[0].pages=[DesignPage(id='p',asset_id='photo',role='photo',composition=[
        LayoutRegion(role='image',asset_id='photo',x=0,y=0,width=1,height=1)])]
    assets['photo'].update(width=1600,height=900)
    if change=='fixed_square': plan.brief.instruction+='，保持1:1方图'
    elif change=='information_role': plan.concepts[0].pages[0].composition.append(LayoutRegion(role='rule',x=0,y=0,width=1,height=.1))
    elif change=='unknown_dimensions': assets['photo'].pop('width')
    elif change=='not_wordless': plan.brief.instruction='保留标题'
    assert apply_native_photo_canvas(plan.brief,plan.concepts[0],assets) is None
    assert (plan.brief.platform.width,plan.brief.platform.height)==(1080,1080)
