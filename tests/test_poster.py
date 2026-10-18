import base64
import io
import xml.etree.ElementTree as ET

import pytest
from pydantic import ValidationError
from PIL import Image

from app.models import TaskInput, RunResult, Asset, ContentBlock, ImagePlacement
from app.poster import PosterBrief, build_poster, render_svg, apply_edits, wrap_text

_png_stream = io.BytesIO()
Image.new('RGB', (64, 64), '#efefe8').save(_png_stream, format='PNG')
PNG = _png_stream.getvalue()


@pytest.mark.parametrize('payload', [b'\x89PNG\r\n\x1a\nwrong', b'\xff\xd8\xffbroken', b'GIF89abroken',
                                   b'RIFF\x00\x00\x00\x00WEBPbroken'])
def test_magic_only_rasters_are_not_embedded(payload):
    from app.poster import _raster_data
    assert _raster_data(payload) is None


def sample():
    task = TaskInput(product='HUAWEI MateBook 14', audience='学生', scene='教室', platform='poster')
    result = RunResult(title='把灵感，留在课堂', intro='从课程任务出发选择你的电脑',
        cta='预约到店体验', blocks=[ContentBlock(text='屏幕表现，现场验证', fact_ids=['f1'], disclosures=['具体配置以实际选购版本为准'])],
        assets=[Asset(id='a1', product=task.product, url='https://secret.example/private-token', source_id='s1', verified=True)],
        images=[ImagePlacement(asset_id='a1', caption='官方产品', position='封面')])
    return task, result


@pytest.mark.parametrize('preset', ['portrait','square','story','landscape','a4'])
@pytest.mark.parametrize('direction', ['minimal','editorial','bold'])
def test_reflow_keeps_product_and_conditions(preset, direction):
    task, result = sample()
    doc = build_poster(task, result, PosterBrief(size_preset=preset,direction=direction))
    assert not [a for a in doc.audit if a['severity']=='error'], doc.audit
    product = next(l for l in doc.layers if l.role=='product')
    assert product.locked and product.asset_id=='a1'
    disclosure = next(l for l in doc.layers if l.role=='disclosure')
    assert disclosure.text==result.blocks[0].disclosures[0] and disclosure.fact_ids==['f1']
    assert 'private-token' not in doc.model_dump_json()
    svg = render_svg(doc, {'a1': PNG})
    ET.fromstring(svg)
    assert 'data:image/png;base64,' in svg
    assert '<image ' in svg and '<text ' in svg
    assert 'https://' not in svg and '草稿' in svg


def test_xml_injection_and_svg_asset_rejected():
    task, result = sample()
    result.title = '</text><script>alert(1)</script>'
    doc = build_poster(task,result,PosterBrief())
    svg = render_svg(doc, {'a1':'data:image/svg+xml;base64,'+base64.b64encode(b'<svg onload="alert(1)"/>').decode()})
    ET.fromstring(svg)
    assert '<script>' not in svg and '&lt;script&gt;' in svg
    assert 'data:image/svg+xml' not in svg
    assert '真实素材尚未载入' in svg


def test_unknown_or_wrong_product_never_used():
    task, result = sample()
    result.assets[0].product = 'OTHER PC'
    doc = build_poster(task,result,PosterBrief())
    assert doc.status=='blocked'
    assert not [l for l in doc.layers if l.role=='product']


def test_locked_layers_and_unsafe_or_overflow_edits_rejected():
    task, result = sample()
    doc = build_poster(task,result,PosterBrief())
    product = next(l for l in doc.layers if l.role=='product')
    with pytest.raises(ValueError,match='锁定'):
        apply_edits(doc,[{'id':product.id,'changes':{'visible':False}}])
    title = next(l for l in doc.layers if l.role=='headline')
    with pytest.raises(ValueError,match='不支持'):
        apply_edits(doc,[{'id':title.id,'changes':{'onclick':'bad'}}])
    with pytest.raises(ValueError,match='溢出'):
        apply_edits(doc,[{'id':title.id,'changes':{'text':'很长的标题'*100}}])
    with pytest.raises(ValueError,match='范围'):
        apply_edits(doc,[{'id':title.id,'changes':{'x':doc.width}}])
    edited = apply_edits(doc,[{'id':title.id,'changes':{'text':'让灵感流动'}}])
    assert edited.version==doc.version+1
    assert next(l for l in edited.layers if l.id==title.id).text=='让灵感流动'
    assert next(l for l in doc.layers if l.id==title.id).text==result.title


def test_long_conditions_retained_and_block_if_overflow():
    task, result = sample()
    condition='必须连接指定型号设备且系统版本满足条件；'*50
    result.blocks[0].disclosures=[condition]
    doc=build_poster(task,result,PosterBrief())
    assert next(l for l in doc.layers if l.role=='disclosure').text==condition
    assert doc.status=='blocked'
    assert any(a['category']=='overflow' for a in doc.audit)


def test_input_colors_and_extreme_canvas_rejected():
    with pytest.raises(ValidationError):
        PosterBrief(accent_color='url(https://evil)')
    with pytest.raises(ValidationError):
        PosterBrief(size_preset='custom',width=4096,height=4096)
    assert PosterBrief(preset='square').height==1080


def test_wrapping_preserves_every_character():
    value='课堂笔记 <快速> & A6000\n真实条件不可丢失'
    assert ''.join(wrap_text(value,100,20)) == value.replace('\n','')

@pytest.mark.parametrize('preset', ['portrait','square','story','landscape','a4'])
def test_three_actual_benefits_and_multiline_subtitle_have_room(preset):
    task,result=sample()
    result.intro='把课程资料和灵感带进课堂，在实际任务中选择适合自己的电脑'
    result.blocks=[ContentBlock(text=text) for text in (
        '课堂记笔记，先现场验证你熟悉的课程软件与外设',
        '把跨设备工作流演示一遍，再决定是否适合自己',
        '屏幕与携带体验，到店按你的真实使用习惯确认')]
    doc=build_poster(task,result,PosterBrief(size_preset=preset))
    assert not [a for a in doc.audit if a['severity']=='error'],doc.audit
    assert all(l.font_size>=18 for l in doc.layers if l.role in ('benefit','subtitle'))


def test_reposition_cannot_overlap_protected_product():
    task,result=sample()
    doc=build_poster(task,result,PosterBrief())
    product=next(l for l in doc.layers if l.role=='product')
    title=next(l for l in doc.layers if l.role=='headline')
    with pytest.raises(ValueError,match='重叠'):
        apply_edits(doc,[{'id':title.id,'changes':{'y':product.y}}])

@pytest.mark.parametrize('preset',['portrait','square','story','landscape','a4'])
@pytest.mark.parametrize('direction',['minimal','editorial','bold'])
def test_generated_background_text_has_contrast_backplates_without_covering_product(preset,direction):
    task,result=sample()
    doc=build_poster(task,result,PosterBrief(size_preset=preset,direction=direction,
        background_mode='generate',background_asset_id='generated-bg'))
    product=next(layer for layer in doc.layers if layer.role=='product')
    plates=[layer for layer in doc.layers if layer.role=='contrast_backplate']
    assert len(plates)==4
    assert all(layer.opacity==.96 and layer.locked for layer in plates)
    assert all(layer.fill==('#102E26' if direction=='bold' else '#F3F0E8') for layer in plates)
    for plate in plates:
        overlap_x=min(plate.x+plate.width,product.x+product.width)-max(plate.x,product.x)
        overlap_y=min(plate.y+plate.height,product.y+product.height)-max(plate.y,product.y)
        assert overlap_x<=0 or overlap_y<=0
    for text in (layer for layer in doc.layers if layer.type=='text' and layer.role!='cta'):
        assert any(plate.x<=text.x and plate.y<=text.y
            and plate.x+plate.width>=text.x+text.width-.1
            and plate.y+plate.height>=text.y+text.height-.1
            and plate.z<text.z for plate in plates)
    assert not [item for item in doc.audit if item['severity']=='error']
    assert 'opacity="0.96"' in render_svg(doc,{'a1':PNG,'generated-bg':PNG})


def test_solid_background_keeps_original_composition_and_opacity_bounds():
    from app.poster import PosterLayer
    task,result=sample()
    doc=build_poster(task,result,PosterBrief())
    assert not any(layer.role=='contrast_backplate' for layer in doc.layers)
    assert all(layer.opacity==1 for layer in doc.layers)
    shape={'id':'shape','type':'shape','x':0,'y':0,'width':10,'height':10}
    for opacity in (-.1,1.1,float('nan')):
        with pytest.raises(ValidationError):
            PosterLayer(**shape,opacity=opacity)
