import base64
from copy import deepcopy
import hashlib
import io
import xml.etree.ElementTree as ET

from PIL import Image, ImageDraw
import pytest
from pydantic import ValidationError

from app.poster import ContactShadow, PosterLayer
from app.design.models import DesignPlan
from app.design.connectors import get_platform_connector
from app.design.compositor import compose_design
from app.design.renderer import render_design_svg
from app.poster_render import render_png


def fixture(shadow=True, opacity=1):
    source=Image.new('RGBA',(240,160),(0,0,0,0))
    ImageDraw.Draw(source).rectangle((10,5,230,150),fill=(180,40,30,255))
    stream=io.BytesIO();source.save(stream,'PNG');raw=stream.getvalue()
    asset={'data_url':'data:image/png;base64,'+base64.b64encode(raw).decode(),
           'sha256':hashlib.sha256(raw).hexdigest(),'source_id':'exact-source','visual_role':'product'}
    region={'id':'subject','role':'image','asset_id':'product','x':.2,'y':.2,'width':.6,'height':.4,'opacity':opacity}
    if shadow:
        region['contact_shadow']={'center_x':.5,'center_y':1,'width':.8,'height':.04,'blur':5,'opacity':.3,'color':'#303030'}
    connector=get_platform_connector('moments').model_copy(update={'width':400,'height':400})
    plan=DesignPlan.model_validate({'brief':{'platform':connector.model_dump(),'asset_ids':['product'],'instruction':'无叠字'},
          'selected_concept_id':'one','concepts':[{'id':'one','name':'authored','visual_thesis':'accurate product grounded by a contact shadow',
          'production_method':'native_assembly','composition':[region],'pages':[{'id':'one','role':'photo','asset_id':'product','composition':[region]}]}]})
    boards=compose_design({}, {}, plan, {'product':asset})
    return boards[0].document, asset


def test_authored_contact_shadow_and_image_opacity_survive_actual_compilation_and_svg():
    doc,asset=fixture(opacity=.8)
    layer=doc.layers[0]
    assert layer.opacity==.8 and layer.contact_shadow.center_y==1
    before=deepcopy(doc.assets)
    svg=render_design_svg(doc)
    root=ET.fromstring(svg);ns={'s':'http://www.w3.org/2000/svg'}
    ellipse=root.find('.//s:ellipse',ns);image=root.find('.//s:image',ns)
    assert ellipse.attrib['data-role']=='contact-shadow'
    assert float(ellipse.attrib['cx'])==200 and float(ellipse.attrib['cy'])==240
    assert float(ellipse.attrib['rx'])==96
    assert float(image.attrib['opacity'])==.8
    assert svg.index('data-role="contact-shadow"') < svg.index('<image')
    assert doc.assets==before and doc.assets['product']['sha256']==asset['sha256']


def test_actual_png_ground_shadow_does_not_repaint_source_product_pixels():
    plain,_=fixture(shadow=False);grounded,_=fixture()
    a=Image.open(io.BytesIO(render_png(render_design_svg(plain),400,400))).convert('RGB')
    b=Image.open(io.BytesIO(render_png(render_design_svg(grounded),400,400))).convert('RGB')
    assert a.getpixel((200,150))==b.getpixel((200,150))==(180,40,30)
    assert sum(b.getpixel((200,244))) < sum(a.getpixel((200,244)))
    assert plain.assets==grounded.assets


def test_contact_shadow_is_optional_bounded_and_only_valid_on_image_layers():
    doc,_=fixture(shadow=False)
    assert doc.layers[0].contact_shadow is None
    with pytest.raises(ValidationError):ContactShadow(blur=200)
    with pytest.raises(ValidationError):ContactShadow(color='url(https://bad)')
    with pytest.raises(ValidationError):PosterLayer(id='text',type='text',x=0,y=0,width=100,height=100,contact_shadow={})


def test_negative_tracking_preserves_authored_size_when_actual_glyphs_fit():
    from app.design.typography import measure, fit_text
    text='HUAWEI MateBook Pro'; maximum=44; tracking=-2
    width=measure(text,maximum,tracking,'sans',700)+.01
    old_text,old_size,_=fit_text(text,width,100,maximum,minimum=24,font_weight=700)
    resolved,size,fits=fit_text(text,width,100,maximum,minimum=24,font_weight=700,tracking=tracking)
    assert old_size < maximum
    assert size==maximum and fits and resolved==text
    assert measure(resolved,size,tracking,'sans',700)<=width


def test_positive_tracking_reduces_size_instead_of_overflowing_same_box():
    from app.design.typography import measure, fit_text
    text='HUAWEI MateBook Pro'; maximum=44; tracking=2
    width=measure(text,maximum,0,'sans',700)+.01
    resolved,size,fits=fit_text(text,width,100,maximum,minimum=24,font_weight=700,tracking=tracking)
    assert 24<=size<maximum and fits
    assert max(measure(line,size,tracking,'sans',700) for line in resolved.splitlines()) <= width
    _,too_small,fit_at_min=fit_text(text,10,20,maximum,minimum=24,font_weight=700,tracking=tracking)
    assert too_small==24 and fit_at_min is False


def test_planner_compact_schema_exposes_contact_shadow_numeric_controls():
    from app.design.planner import _compact_schema_contract
    schema=_compact_schema_contract()
    assert 'contact_shadow' in schema['region']['properties']
    assert set(schema['contact_shadow']['properties']) == {'center_x','center_y','width','height','blur','opacity','color'}
    assert schema['contact_shadow']['properties']['blur']['maximum']==80
