"""Exercise actual SVG paint order and Chromium pixels, without AI providers."""
import base64
import io
from xml.etree import ElementTree as ET

from PIL import Image

from app.design.renderer import render_design_svg, try_full_frame_png
from app.poster import PosterBrief, PosterCanvas, PosterDocument, PosterLayer
from app.poster_render import render_png


def document(layers, assets=None):
    return PosterDocument(width=320, height=320,
        canvas=PosterCanvas(width=320, height=320, background='#FFFFFF'),
        brief=PosterBrief(size_preset='custom', width=320, height=320),
        layers=layers, assets=assets or {})


def solid_asset(color='#DD2222', size=(120,120)):
    stream=io.BytesIO()
    Image.new('RGB',size,color).save(stream,format='PNG')
    return {'data_url':'data:image/png;base64,'+base64.b64encode(stream.getvalue()).decode()}


def raster(doc):
    svg=render_design_svg(doc)
    return svg, Image.open(io.BytesIO(render_png(svg,320,320))).convert('RGB')


def test_actual_type_behind_product_and_front_type_have_different_pixels():
    product=PosterLayer(id='product',type='image',asset_id='real',role='product',locked=True,
        x=100,y=100,width=120,height=120,z=10)
    title=PosterLayer(id='headline',type='text',text='MMMM',font_size=90,font_weight=800,
        color='#000000',x=40,y=70,width=250,height=180,z=5)
    # Deliberately put the input list in the wrong order: z must govern paint.
    doc=document([product,title],{'real':solid_asset()})
    svg,behind=raster(doc)
    root=ET.fromstring(svg)
    painted=[child.get('id') for child in root if child.tag.endswith('}g')]
    assert painted==['headline','product']
    assert next(child for child in root if child.get('id')=='headline').find('{http://www.w3.org/2000/svg}path') is not None
    covered=behind.crop((102,102,218,218))
    assert covered.getcolors(maxcolors=320*320)==[(116*116,(221,34,34))]
    # Editing z must genuinely expose outlined letters over the red pixels.
    doc.layers[1].z=15
    _,front=raster(doc)
    assert sum(count for count,(r,g,b) in front.crop((102,102,218,218)).getcolors(maxcolors=320*320)
               if r<50 and g<50 and b<50)>100
    assert front.tobytes()!=behind.tobytes()


def test_rotation_is_real_two_dimensional_geometry_not_only_serialized_metadata():
    image=PosterLayer(id='rotated-product',type='image',asset_id='real',role='product',locked=True,
        x=80,y=140,width=160,height=40,z=10,rotation=90)
    svg,rotated=raster(document([image],{'real':solid_asset('#2255DD',(160,40))}))
    group=next(child for child in ET.fromstring(svg) if child.get('id')=='rotated-product')
    assert group.get('transform')=='rotate(90.0 160.0 160.0)'
    assert rotated.getpixel((150,100))==(34,85,221)
    assert rotated.getpixel((100,150))==(255,255,255)
    image.rotation=0
    _,unrotated=raster(document([image],{'real':solid_asset('#2255DD',(160,40))}))
    assert unrotated.getpixel((150,100))==(255,255,255)
    assert unrotated.getpixel((100,150))==(34,85,221)


def test_image_shadow_references_survive_global_order_and_change_actual_pixels():
    image=PosterLayer(id='product-shadow',type='image',asset_id='real',role='product',locked=True,
        x=100,y=100,width=80,height=80,z=10,
        shadow_opacity=.6,shadow_blur=2,shadow_dx=20,shadow_dy=20)
    text=PosterLayer(id='type-shadow',type='text',text='A',font_size=70,color='#000000',
        x=15,y=15,width=80,height=85,z=20,rotation=-8,
        shadow_color='#2255DD',shadow_opacity=.4,shadow_blur=3,shadow_dx=5,shadow_dy=5)
    svg,with_shadow=raster(document([text,image],{'real':solid_asset()}))
    root=ET.fromstring(svg)
    ns={'s':'http://www.w3.org/2000/svg'}
    filters={f.get('id') for f in root.findall('.//s:filter',ns)}
    groups={g.get('id'):g for g in root if g.tag.endswith('}g')}
    for key in ('product-shadow','type-shadow'):
        reference=groups[key].get('filter')
        assert reference.startswith('url(#') and reference[5:-1] in filters
    assert groups['type-shadow'].get('transform').startswith('rotate(-8.0 ')
    # Beyond the product edge: the shadow is a visible painted result.
    shaded=with_shadow.getpixel((190,190))
    assert max(shaded)-min(shaded)<3 and 50<min(shaded)<240
    image.shadow_opacity=0
    _,without=raster(document([text,image],{'real':solid_asset()}))
    assert without.getpixel((190,190))==(255,255,255)


def test_finished_png_with_visible_shadow_must_not_skip_composition():
    asset=solid_asset(size=(320,320))
    layer=PosterLayer(id='finished',type='image',asset_id='real',role='generated_design',locked=True,
        x=0,y=0,width=320,height=320,shadow_opacity=.2,shadow_blur=5)
    assert try_full_frame_png(document([layer],{'real':asset})) is None


def test_hero_character_can_genuinely_occupy_a_campaign_canvas_above_old_size_limit():
    title = PosterLayer(id='hero', type='text', role='headline', text='夜',
        x=80, y=40, width=900, height=920, font_size=785, line_height=1,
        font_family='serif', font_weight=800, color='#000000')
    doc = PosterDocument(width=1080, height=1080,
        canvas=PosterCanvas(width=1080,height=1080,background='#FFFFFF'),
        brief=PosterBrief(size_preset='custom',width=1080,height=1080), layers=[title])
    svg = render_design_svg(doc)
    image = Image.open(io.BytesIO(render_png(svg,1080,1080))).convert('RGB')
    mask = image.convert('L').point(lambda p: 255 if p < 100 else 0)
    box = mask.getbbox()
    assert box and box[2]-box[0] > 650 and box[3]-box[1] > 650
    assert 0 <= box[0] < box[2] <= 1080 and 0 <= box[1] < box[3] <= 1080
    assert title.text == '夜' and 'aria-label="夜"' in svg


def test_actual_hidden_caption_is_a_hard_occlusion_error_while_artistic_headline_remains_allowed():
    from app.design.compositor import functional_text_occlusion_checks
    product=PosterLayer(id='product',type='image',asset_id='real',role='product',locked=True,
        x=90,y=90,width=140,height=140,z=10)
    caption=PosterLayer(id='caption',type='text',role='caption',text='MMM',font_size=50,
        x=100,y=100,width=120,height=110,z=5,color='#000000')
    doc=document([caption,product],{'real':solid_asset()})
    _,pixels=raster(doc)
    assert pixels.crop((100,100,220,210)).getcolors(maxcolors=320*320)==[(120*110,(221,34,34))]
    checks=functional_text_occlusion_checks(doc)
    assert checks[0]['category']=='functional_text_occlusion' and checks[0]['severity']=='error'
    assert checks[0]['text_layer_id']=='caption'
    caption.role='headline'
    assert functional_text_occlusion_checks(doc)==[]
    product.role='photograph'
    assert functional_text_occlusion_checks(doc)[0]['severity']=='error'
    caption.role='caption';caption.z=20
    assert functional_text_occlusion_checks(doc)==[]


def test_contact_and_outline_shadows_keep_independent_references_and_real_pixels():
    image=PosterLayer(id='both-shadows',type='image',asset_id='real',role='product',locked=True,
        x=100,y=100,width=80,height=80,z=10,shadow_opacity=.6,shadow_blur=2,shadow_dx=20,shadow_dy=20,
        contact_shadow={'center_x':.5,'center_y':1.0125,'width':.8,'height':.06,'blur':2,'opacity':.6,'color':'#303030'})
    svg,both=raster(document([image],{'real':solid_asset()}))
    ns={'s':'http://www.w3.org/2000/svg'};root=ET.fromstring(svg)
    group=next(g for g in root if g.get('id')=='both-shadows')
    assert group.get('filter') is None
    product=group.find('s:image',ns);ground=group.find('s:ellipse',ns)
    assert product is not None and ground is not None
    filters={f.get('id'):f for f in root.findall('.//s:filter',ns)}
    for element,operation in ((product,'feDropShadow'),(ground,'feGaussianBlur')):
        reference=element.get('filter')
        assert reference.startswith('url(#')
        assert filters[reference[5:-1]].find('s:'+operation,ns) is not None
    assert product.get('filter')!=ground.get('filter')
    assert both.getpixel((140,140))==(221,34,34)
    assert max(both.getpixel((190,190)))<240
    image.contact_shadow=None
    _,outline_only=raster(document([image],{'real':solid_asset()}))
    assert sum(both.getpixel((112,181)))<sum(outline_only.getpixel((112,181)))
