import asyncio
import copy
import math

from app.design.compositor import compose_design
from app.design.planner import plan_design


def fixture():
    record={'id':'identity-source','title':'换个角度看生活','intro':'带上自己的想法',
            'images':[{'asset_id':'real'}]}
    plan=asyncio.run(plan_design({'product':'Mate 80'},record,platform='朋友圈'))
    return record,plan,{'real':{'visual_role':'product'}}


def identity(plan,record,assets):
    board=compose_design({},record,plan,assets)[0]
    return board.id,board.document.id,tuple(layer.id for layer in board.document.layers)


def test_same_execution_has_same_identity_without_cross_concept_collisions():
    record,plan,assets=fixture()
    identities=[]
    for concept in plan.concepts:
        candidate=plan.model_copy(update={'selected_concept_id':concept.id})
        actual=identity(candidate,record,assets)
        assert actual==identity(candidate.model_copy(deep=True),copy.deepcopy(record),copy.deepcopy(assets))
        identities.append(actual)
    assert len({row[0] for row in identities})==len(plan.concepts)
    assert len({row[1] for row in identities})==len(plan.concepts)
    assert not set(identities[0][2]) & set(identities[1][2])


def test_actual_execution_changes_get_fresh_identity_even_if_concept_id_reused():
    record,plan,assets=fixture()
    plan.selected_concept_id=plan.concepts[0].id
    original=identity(plan,record,assets)
    def changed(mutator):
        revision=plan.model_copy(deep=True)
        mutator(revision.concepts[0],revision)
        assert identity(revision,record,assets)[0]!=original[0]
    changed(lambda c,p:setattr(c.pages[0],'id','new-page'))
    changed(lambda c,p:setattr(c.pages[0].composition[0],'x',.075))
    changed(lambda c,p:setattr(c,'background','#102030'))
    changed(lambda c,p:setattr(c.art_direction,'material_and_light','A new directional light'))
    changed(lambda c,p:setattr(c.pages[0],'visual_brief','A new abstract spatial relation'))
    changed(lambda c,p:p.brief.texts['title'].update(text='不同的审定短标题'))


def test_readable_font_floor_blocks_overflow_instead_of_shrinking_to_small_print():
    record,plan,assets=fixture()
    plan.selected_concept_id=plan.concepts[0].id
    concept=plan.concepts[0]
    # A long approved caption in a tiny authored box cannot become tiny legal
    # print. The layout must be corrected while preserving the actual wording.
    plan.brief.texts['caption']={'text':'保留完整审定说明，不要通过缩成极小文字挤进狭小区域。'*4}
    from app.design.models import LayoutRegion
    concept.pages[0].composition=[
        LayoutRegion(role='headline',text_id='title',x=.08,y=.06,width=.84,height=.13,font_scale=.012),
        LayoutRegion(role='caption',text_id='caption',x=.08,y=.8,width=.84,height=.04,font_scale=.012),
        LayoutRegion(role='image',asset_id='real',x=.1,y=.25,width=.8,height=.5),
        LayoutRegion(role='brand',x=.08,y=.87,width=.84,height=.08)]
    concept.pages[0].text_ids=['title','caption']
    board=compose_design({},record,plan,assets)[0]
    title=next(l for l in board.document.layers if l.role=='headline')
    caption=next(l for l in board.document.layers if l.role=='caption')
    assert title.font_size>=math.ceil(board.document.width*.05)
    assert caption.font_size>=math.ceil(board.document.width*.033)
    assert caption.text.replace('\n','')==plan.brief.texts['caption']['text']
    assert board.document.status=='blocked'
    assert any(row['category']=='overflow' for row in board.document.audit)


def test_small_authored_text_boxes_expand_into_existing_blank_space_with_same_copy_and_product():
    record,plan,assets=fixture()
    plan.selected_concept_id=plan.concepts[0].id
    concept=plan.concepts[0]
    plan.brief.texts['title']['text']='你午休时会选哪种镜头来记录身边？'
    plan.brief.texts['caption']={'text':'到店时，拿Mate 80试着分别用主摄、长焦、超广角拍身边物品，观察画面变化。'}
    from app.design.models import LayoutRegion
    image=LayoutRegion(role='image',asset_id='real',x=.3,y=.35,width=.4,height=.35)
    concept.pages[0].composition=[
        LayoutRegion(role='headline',text_id='title',x=.19,y=.18,width=.62,height=.11,line_height=1.12,font_scale=.05),
        image,
        LayoutRegion(role='caption',text_id='caption',x=.13,y=.81,width=.36,height=.12,line_height=1.25,font_scale=.033),
        LayoutRegion(role='brand',text_id='identity',x=.67,y=.93,width=.18,height=.05,line_height=1.15,font_scale=.024)]
    plan.brief.texts['identity']={'text':'HUAWEI Mate 80'}
    concept.pages[0].text_ids=['title','caption','identity']
    board=compose_design({},record,plan,assets)[0]
    assert board.document.status=='draft' and not board.document.audit
    from app.design_review import audit_design_document
    assert not [row for row in audit_design_document(board.document) if row['severity']=='error']
    product=next(layer for layer in board.document.layers if layer.type=='image')
    assert (product.x,product.y,product.width,product.height)==(image.x*1080,image.y*1080,image.width*1080,image.height*1080)
    for layer in board.document.layers:
        if layer.type!='text':continue
        metric=board.typography[layer.id]
        assert layer.text.replace('\n','')==plan.brief.texts[metric['source_text_id']]['text']
        assert metric['layout_adjustment']['kind']=='minimum_safe_blank_space_expansion'
        assert layer.x==metric['layout_adjustment']['original_box'][0]
        assert layer.y==metric['layout_adjustment']['original_box'][1]


def test_right_aligned_brand_expansion_keeps_its_right_anchor():
    record,plan,assets=fixture()
    plan.selected_concept_id=plan.concepts[0].id
    from app.design.models import LayoutRegion
    brand=LayoutRegion(role='brand',x=.6,y=.85,width=.18,height=.06,align='right',font_scale=.024)
    plan.concepts[0].pages[0].composition=[brand]
    plan.brief.product='HUAWEI Mate 80'
    board=compose_design({},record,plan,assets)[0]
    layer=board.document.layers[0]
    assert layer.width>brand.width*1080
    assert abs(layer.x+layer.width-(brand.x+brand.width)*1080)<.001
