from app.design.planner import _revision_constraints


def previous():
    return {'previous_design': {'selected_concept_id': 'chosen', 'concepts': [{
        'id': 'chosen', 'background': '#112233', 'foreground': '#FFFFFF',
        'accent': '#226655', 'font_role': 'sans'}]}}


def test_local_typographic_adjustment_preserves_existing_visual_identity():
    result = _revision_constraints(previous(), '标题间距稍微大一点')
    assert result['locked_values']['background'] == '#112233'
    assert result['locked_values']['font_role'] == 'sans'


def test_new_creative_commission_releases_old_palette_and_font_but_keeps_explicit_choices():
    result = _revision_constraints(previous(), '重新设计，从创意概念和字体上重做')
    assert result['mode'] == 'new_concept' and result['locked_values'] == {}
    kept = _revision_constraints(previous(), '重新设计，但保留深色背景和字体')
    assert kept['locked_values']['background'] == '#112233'
    assert kept['locked_values']['font_role'] == 'sans'
