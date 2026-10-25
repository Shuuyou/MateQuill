"""Approved semantic units guide measured wraps without rewriting public copy."""
from app.design.typography import fit_text, measure, semantic_wrap
from app.design.compositor import compose_design
from app.design.models import DesignPlan
from app.design.connectors import get_platform_connector


UNITS = ['下课后，', '看看心率，', '也问问感受']
TEXT = ''.join(UNITS)


def test_health_headline_fallback_keeps_common_words_atomic():
    text='合上书，也关心昨晚的睡眠'
    for preferred in (None, [4]):
        wrapped,size,fits=fit_text(text,399.6,295.2,65,minimum=40,
            font_weight=700,preferred_breaks=preferred)
        assert fits and wrapped.replace('\n','')==text
        assert '关\n心' not in wrapped and '睡\n眠' not in wrapped
    wrapped=semantic_wrap('看看心率，再关心睡眠',240,65,font_weight=700)
    assert '心\n率' not in '\n'.join(wrapped)


def board(*, units=UNITS, selected=TEXT, tracking=0, height=.31, width=.37):
    region={'id':'headline-region','role':'headline','text_id':'headline',
        'x':.565,'y':.245,'width':width,'height':height,'font_scale':.05,
        'font_fit':'authored','font_weight':700,'line_height':1.2,'letter_spacing':tracking}
    plan=DesignPlan.model_validate({'brief':{'platform':get_platform_connector('xiaohongshu').model_dump(),
        'texts':{'headline':{'text':TEXT,'fact_ids':['actual-fact-id']}}},
        'selected_concept_id':'one','concepts':[{'id':'one','name':'Authored title',
            'composition':[region],
            'visual_thesis':'The approved title shares the frame with the source product',
            'pages':[{'id':'cover','role':'cover','text_ids':['headline'],'composition':[region]}]}],
        'provenance':{'copy_craft':[{'card_id':'cover','selected_headline':selected,'headline_units':units}]}})
    return compose_design({}, {}, plan, {})[0]


def test_actual_399px_54px_title_keeps_approved_semantic_units():
    result=semantic_wrap(TEXT,399.6,54,font_weight=700,preferred_breaks=[4,9])
    assert result==UNITS
    assert ''.join(result)==TEXT
    assert all(measure(line,54,font_weight=700)<=399.6 for line in result)
    rendered=board()
    layer=rendered.document.layers[0]
    assert layer.text=='\n'.join(UNITS)
    assert layer.font_size==54 and layer.fact_ids==['actual-fact-id']
    assert rendered.typography[layer.id]['preferred_breaks']==[4,9]
    assert rendered.typography[layer.id]['fits'] is True


def test_tracking_and_orphan_reflow_keep_the_same_preferred_boundaries():
    rendered=board(tracking=-.5)
    layer=rendered.document.layers[0]
    assert layer.text=='\n'.join(UNITS)
    assert layer.text.replace('\n','')==TEXT
    assert rendered.typography[layer.id]['fits'] is True


def test_unmatched_or_changed_craft_text_never_rewrites_or_controls_public_copy():
    for rendered in [board(units=['另一段','未经批准的文字']), board(selected='另一个标题')]:
        layer=rendered.document.layers[0]
        assert layer.text.replace('\n','')==TEXT
        assert 'preferred_breaks' not in rendered.typography[layer.id]


def test_soft_preferences_fall_back_when_the_authored_height_cannot_fit_all_units():
    text='这是第一段，这是第二段，这是最后一段'
    result,size,fits=fit_text(text,320,72,30,30,line_height=1.2,preferred_breaks=[6,12])
    assert fits and size==30
    assert result.replace('\n','')==text and len(result.splitlines())==2


def test_long_unit_can_wrap_inside_it_without_forcing_width_overflow():
    text='这是一段无法完整放入窄框的比较长的内容，后面继续。'
    lines=semantic_wrap(text,130,30,preferred_breaks=[20])
    assert ''.join(lines)==text
    assert all(measure(line,30)<=130 for line in lines)


def test_atomic_model_token_and_explicit_newline_remain_intact():
    text='HUAWEI MateBook Pro\n第二行内容'
    expected=semantic_wrap(text,180,30)
    assert semantic_wrap(text,180,30,preferred_breaks=[3])==expected
    assert expected[0]=='HUAWEI MateBook Pro'


def test_default_wrap_behavior_is_unchanged_without_approved_units():
    assert semantic_wrap(TEXT,399.6,54,font_weight=700)==semantic_wrap(
        TEXT,399.6,54,font_weight=700,preferred_breaks=[])
