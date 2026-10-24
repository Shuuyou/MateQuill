from app.design.compositor import compose_design
from app.design.models import DesignPlan
from app.design.connectors import get_platform_connector


def make_board(font_scale=None, *, overflow=False):
    region={'id':'label','role':'caption','text_id':'approved','x':.1,'y':.1,'width':.8,'height':42/1080,'line_height':1.15,
            'font_fit':'authored' if font_scale is not None else 'adaptive'}
    if font_scale is not None:region['font_scale']=font_scale
    rows=[region]
    if overflow:
        region.update(width=80/1080,height=24/1080)
        rows += [{'id':'right','role':'image','asset_id':'source','x':.1+80/1080,'y':.1,'width':.7,'height':.5},
                 {'id':'below','role':'image','asset_id':'source','x':.1,'y':.1+24/1080,'width':80/1080,'height':.5}]
    text='颜色示意' if not overflow else '这是一句必须完整保留的批准限定文字，不得缩成小字或截断'
    plan=DesignPlan.model_validate({'brief':{'platform':get_platform_connector('moments').model_dump(),
        'texts':{'approved':{'text':text}}},'selected_concept_id':'one',
        'concepts':[{'id':'one','name':'fine typography','visual_thesis':'Readable caption',
        'composition':rows,'pages':[{'id':'page','role':'cover','composition':rows}]}]})
    return compose_design({}, {}, plan, {'source':{'visual_role':'product'}})[0]


def test_explicit_23px_caption_is_preserved_in_same_box_while_default_stays_36px():
    authored=make_board(23/1080);default=make_board()
    assert authored.document.layers[0].font_size==23
    assert default.document.layers[0].font_size==36
    assert authored.document.layers[0].width==default.document.layers[0].width
    assert authored.document.layers[0].font_family==default.document.layers[0].font_family
    assert authored.document.layers[0].text==default.document.layers[0].text
    assert next(iter(authored.typography.values()))['fits'] is True


def test_explicit_sub20px_caption_uses_hard_floor_and_keeps_approved_words():
    board=make_board(15/1080)
    assert board.document.layers[0].font_size==20
    assert board.document.layers[0].text=='颜色示意'
    assert next(iter(board.typography.values()))['fits'] is True


def test_explicit_size_still_reports_real_overflow_when_floor_and_safe_space_cannot_fit():
    board=make_board(15/1080,overflow=True)
    layer=board.document.layers[0]
    assert layer.font_size==20
    assert next(iter(board.typography.values()))['fits'] is False
    assert board.document.status=='blocked'
    assert any(issue['category']=='overflow' and issue['severity']=='error' for issue in board.document.audit)
    assert layer.text.replace('\n','')=='这是一句必须完整保留的批准限定文字，不得缩成小字或截断'
