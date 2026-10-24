"""Stroke-only native geometry remains open in actual Chromium pixels."""
import io
import re

from PIL import Image
import pytest

from app.design.compositor import compose_design
from app.design.connectors import get_platform_connector
from app.design.models import DesignBrief, DesignConcept, DesignPage, DesignPlan, LayoutRegion
from app.design.renderer import render_design_svg
from app.design.review_schema import visual_review_schema
from app.poster import PosterLayer
from app.poster_render import render_png

BRACKETS = 'M0 25 L0 0 L25 0 M75 0 L100 0 L100 25 M100 75 L100 100 L75 100 M25 100 L0 100 L0 75'


def compile_frame(fill='none', stroke=''):
    region = LayoutRegion(role='shape', x=.125, y=.125, width=.75, height=.75,
        geometry='path', path_d=BRACKETS, fill=fill, stroke=stroke, stroke_width=2)
    connector = get_platform_connector('poster').model_copy(update={'width': 320, 'height': 320})
    concept = DesignConcept(id='open-brackets', name='开放折角', visual_thesis='由开放线条表达取景关系',
        composition=[region], pages=[DesignPage(id='one', role='cover', composition=[region])],
        background='#123456', accent='#55CCAA')
    plan = DesignPlan(brief=DesignBrief(platform=connector), concepts=[concept], selected_concept_id=concept.id)
    return compose_design({}, {}, plan, {})[0].document


def test_general_no_fill_shape_compiles_with_hex_stroke_fallback():
    document = compile_frame()
    assert document.layers[0].fill == 'none'
    assert document.layers[0].stroke == '#55CCAA'
    assert 'fill="none"' in render_design_svg(document)
    assert 'stroke="none"' not in render_design_svg(document)
    assert compile_frame(stroke='#112233').layers[0].stroke == '#112233'


def test_real_chromium_open_corner_does_not_implicitly_fill_triangle():
    outline = compile_frame()
    filled = compile_frame(fill='#55CCAA')
    outline_png = render_png(render_design_svg(outline), 320, 320)
    filled_png = render_png(render_design_svg(filled), 320, 320)
    with Image.open(io.BytesIO(outline_png)) as a, Image.open(io.BytesIO(filled_png)) as b:
        # Centre remains transparent to the native canvas; importantly, the
        # interior of each open L also stays clear instead of closing diagonally.
        assert a.convert('RGB').getpixel((160, 160)) == (18, 52, 86)
        assert a.convert('RGB').getpixel((64, 64)) == (18, 52, 86)
        assert b.convert('RGB').getpixel((64, 64)) == (85, 204, 170)
        # The explicit stroke is still drawn, so this is not a missing shape.
        assert a.convert('RGB').getpixel((41, 70)) == (85, 204, 170)


@pytest.mark.parametrize('changes', [
    {'fill': 'none', 'fill_style': 'linear'},
    {'fill': 'none', 'fill_style': 'radial'},
    {'stroke': 'none'}, {'color': 'none'}, {'gradient_end': 'none'}, {'shadow_color': 'none'},
])
def test_none_is_only_a_solid_fill_not_a_general_color_escape(changes):
    with pytest.raises(ValueError):
        PosterLayer(id='shape', type='shape', x=0, y=0, width=100, height=100, **changes)


@pytest.mark.parametrize('role', ['headline', 'body', 'image', 'brand', 'caption', 'kicker'])
def test_layout_none_fill_is_exclusive_to_shape_and_rule(role):
    with pytest.raises(ValueError, match='no_fill_requires_solid_shape_or_rule'):
        LayoutRegion(role=role, x=0, y=0, width=1, height=1, fill='none')


def test_existing_hex_and_closed_filled_shapes_remain_supported():
    layer = PosterLayer(id='filled', type='shape', x=0, y=0, width=100, height=100,
        geometry='path', path_d='M0 0 L100 0 L100 100 Z', fill='#AABBCC', fill_style='linear')
    assert layer.fill == '#AABBCC'
    assert LayoutRegion(role='rule', x=0, y=0, width=1, height=.1, fill='none').fill == 'none'


def test_refinement_wire_schema_can_preserve_stroke_only_fill():
    fill = visual_review_schema(marketing_required=True)['properties']['edits']['items']['properties']['changes']['properties']['fill']
    pattern = fill['anyOf'][0]['pattern']
    assert re.fullmatch(pattern, 'none')
    assert re.fullmatch(pattern, '#AABBCC')
    assert not re.fullmatch(pattern, 'transparent')
