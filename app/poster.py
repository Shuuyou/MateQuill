"""Editable, evidence-aware poster documents and network-free SVG rendering.

The renderer never fetches URLs or reads paths. Raster bytes are supplied by the
HTTP boundary after its provenance checks; product photography is a locked layer.
"""
from __future__ import annotations

import base64
import html
import re
import unicodedata
from collections.abc import Callable, Mapping
from typing import Literal
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field, AliasChoices, model_validator

PRESETS = {'portrait': (1080, 1440), 'square': (1080, 1080), 'story': (1080, 1920),
           'landscape': (1920, 1080), 'a4': (1240, 1754)}
COLOR = re.compile(r'^#[0-9a-fA-F]{6}$')


class PosterBrief(BaseModel):
    model_config = ConfigDict(extra='forbid', populate_by_name=True, allow_inf_nan=False)
    purpose: str = Field(default='门店引流', max_length=200)
    size_preset: Literal['portrait', 'square', 'story', 'landscape', 'a4', 'custom'] = Field(
        default='portrait', validation_alias=AliasChoices('size_preset', 'preset'))
    width: int = Field(default=1080, ge=320, le=4096)
    height: int = Field(default=1440, ge=320, le=4096)
    direction: Literal['minimal', 'editorial', 'bold'] = 'minimal'
    style: str = Field(default='', max_length=500)
    background_mode: Literal['solid', 'generate'] = 'solid'
    background_color: str = '#F3F0E8'
    accent_color: str = '#17654D'
    headline: str = Field(default='', max_length=240)
    subtitle: str = Field(default='', max_length=600)
    cta: str = Field(default='', max_length=240)
    brand_name: str = Field(default='', max_length=100)
    background_asset_id: str = Field(default='', max_length=100)
    image_model: str = Field(default='', max_length=200)

    @model_validator(mode='after')
    def validate_design(self):
        if self.size_preset != 'custom':
            self.width, self.height = PRESETS[self.size_preset]
        if not COLOR.fullmatch(self.background_color) or not COLOR.fullmatch(self.accent_color):
            raise ValueError('颜色须使用六位 HEX 格式')
        if self.width * self.height > 12_000_000:
            raise ValueError('画布不能超过1200万像素')
        return self


class PosterCanvas(BaseModel):
    width: int = Field(ge=320, le=4096)
    height: int = Field(ge=320, le=4096)
    background: str = '#F3F0E8'


class ContactShadow(BaseModel):
    """Authored ground-plane ellipse relative to an image region, not a relit product."""
    model_config = ConfigDict(extra='forbid', allow_inf_nan=False)
    center_x: float = Field(default=.5, ge=-.5, le=1.5)
    center_y: float = Field(default=1, ge=-.5, le=1.5)
    width: float = Field(default=.8, gt=0, le=2)
    height: float = Field(default=.025, gt=0, le=.5)
    blur: float = Field(default=6, ge=0, le=80)
    opacity: float = Field(default=.18, ge=0, le=.6)
    color: str = Field(default='#303030', pattern=r'^#[0-9A-Fa-f]{6}$')


class PosterLayer(BaseModel):
    model_config = ConfigDict(extra='forbid', allow_inf_nan=False)
    id: str
    type: Literal['text', 'image', 'shape']
    name: str = ''
    x: float = Field(ge=0)
    y: float = Field(ge=0)
    width: float = Field(gt=0)
    height: float = Field(gt=0)
    z: int = 0
    rotation: float = Field(default=0, ge=-180, le=180)
    shadow_color: str = '#000000'
    shadow_opacity: float = Field(default=0, ge=0, le=.6)
    shadow_blur: float = Field(default=0, ge=0, le=80)
    shadow_dx: float = Field(default=0, ge=-200, le=200)
    shadow_dy: float = Field(default=0, ge=-200, le=200)
    contact_shadow: ContactShadow | None = None
    locked: bool = False
    visible: bool = True
    text: str = Field(default='', max_length=12000)
    font_size: float = Field(default=32, ge=8, le=1600)
    font_weight: Literal[400, 500, 600, 700, 800] = 400
    color: str = '#172E27'
    align: Literal['left', 'center', 'right'] = 'left'
    fill: str = Field(default='#FFFFFF', pattern=r'^(?:#[0-9A-Fa-f]{6}|none)$')
    radius: float = Field(default=0, ge=0, le=400)
    opacity: float = Field(default=1, ge=0, le=1)
    geometry: Literal['rect','ellipse','line','path'] = 'rect'
    path_d: str = Field(default='',max_length=5000)
    stroke: str = '#172E27'
    stroke_width: float = Field(default=0,ge=0,le=100)
    fill_style: Literal['solid','linear','radial'] = 'solid'
    gradient_end: str = '#FFFFFF'
    font_family: Literal['sans','serif','mono','display'] = 'sans'
    letter_spacing: float = Field(default=0,ge=-6,le=30)
    line_height: float = Field(default=1.3,ge=.9,le=1.8)
    asset_id: str = ''
    role: str = ''
    fact_ids: list[str] = Field(default_factory=list)
    source_ids: list[str] = Field(default_factory=list)

    @model_validator(mode='after')
    def validate_colors(self):
        if self.contact_shadow is not None and self.type != 'image':
            raise ValueError('contact_shadow_requires_image')
        if not all(COLOR.fullmatch(c) for c in (self.color,self.stroke,self.gradient_end,self.shadow_color)):
            raise ValueError('图层颜色须使用六位 HEX 格式')
        if self.fill == 'none':
            if self.fill_style != 'solid':
                raise ValueError('无填充图层不能同时使用渐变填充')
        elif not COLOR.fullmatch(self.fill):
            raise ValueError('图层填充须使用六位 HEX 或 none')
        if self.path_d and not re.fullmatch(r'[MmLlHhVvCcSsQqTtAaZz0-9.,+\-\s]+',self.path_d):
            raise ValueError('矢量路径只能包含绘图命令与坐标')
        return self


class PosterDocument(BaseModel):
    model_config = ConfigDict(extra='forbid', allow_inf_nan=False)
    id: str = Field(default_factory=lambda: uuid4().hex)
    version: int = 1
    width: int
    height: int
    canvas: PosterCanvas
    brief: PosterBrief
    layers: list[PosterLayer]
    assets: dict[str, dict] = Field(default_factory=dict)
    audit: list[dict] = Field(default_factory=list)
    region_bindings: dict[str, str] = Field(default_factory=dict, max_length=20)
    status: Literal['draft', 'ready', 'blocked'] = 'draft'
    source_run_id: str = ''


def _data(obj):
    return obj.model_dump() if hasattr(obj, 'model_dump') else dict(obj)


def _units(char):
    return 1.0 if unicodedata.east_asian_width(char) in ('W', 'F') else 0.56


def wrap_text(text: str, width: float, font_size: float, letter_spacing: float=0) -> list[str]:
    """Keep every character; wrapping never truncates commercial qualifiers."""
    capacity = max(1, width / font_size)
    lines = []
    for paragraph in text.split('\n'):
        line, used = '', 0.0
        for char in paragraph:
            weight = max(.1,_units(char)+letter_spacing/font_size)
            if line and used + weight > capacity:
                lines.append(line)
                line, used = '', 0
            line += char
            used += weight
        lines.append(line)
    return lines


def _fit(layer: PosterLayer, minimum=14):
    if layer.type != 'text':
        return
    while layer.font_size > minimum:
        if len(wrap_text(layer.text, layer.width, layer.font_size,layer.letter_spacing)) * layer.font_size * layer.line_height <= layer.height:
            break
        layer.font_size = max(minimum, layer.font_size - 1)


def audit_document(document: PosterDocument) -> list[dict]:
    issues = []
    def add(severity, category, message):
        issues.append({'severity': severity, 'category': category, 'message': message})
    ids = set()
    products = []
    for layer in document.layers:
        if layer.id in ids:
            add('error', 'layer', '图层ID重复')
        ids.add(layer.id)
        if layer.x + layer.width > document.canvas.width + .1 or layer.y + layer.height > document.canvas.height + .1:
            add('error', 'bounds', f'{layer.name or layer.id} 超出画布范围')
        if layer.role == 'product':
            products.append(layer)
            if not layer.locked or not layer.visible or layer.type != 'image':
                add('error', 'product', '产品图须为可见的锁定独立图片层')
        if layer.type == 'text' and layer.visible:
            if len(wrap_text(layer.text, layer.width, layer.font_size,layer.letter_spacing)) * layer.font_size * layer.line_height > layer.height + .1:
                add('error', 'overflow', f'{layer.name or layer.id} 文字溢出，请精简文案或调整字号与布局')
            if layer.font_size < 18:
                add('warning', 'legibility', f'{layer.name or layer.id} 字号较小，发布前请按实际尺寸检查可读性')
    # Disjoint layout regions are intentional. Backgrounds and button shapes
    # may overlap content, but visible text and the protected product cannot.
    content = [layer for layer in document.layers if layer.visible and
               (layer.type == 'text' or layer.role == 'product')]
    for index, first in enumerate(content):
        for second in content[index+1:]:
            overlap_w = min(first.x+first.width,second.x+second.width)-max(first.x,second.x)
            overlap_h = min(first.y+first.height,second.y+second.height)-max(first.y,second.y)
            if overlap_w > .1 and overlap_h > .1:
                add('error','overlap',f'{first.name or first.id} 与 {second.name or second.id} 内容区域重叠')
    if len(products) != 1:
        add('error', 'product', '海报必须包含一个所选型号的真实产品图片层')
    if document.width != document.canvas.width or document.height != document.canvas.height:
        add('error', 'canvas', '画布尺寸不一致')
    return issues


def build_poster(task, result, brief: PosterBrief | dict) -> PosterDocument:
    brief = brief if isinstance(brief, PosterBrief) else PosterBrief.model_validate(brief)
    task, result = _data(task), _data(result)
    w, h = brief.width, brief.height
    m, scale = w * .065, min(w / 1080, h / 1440)
    accent = brief.accent_color
    dark = '#172E27'
    layers = []
    def layer(type, name, x, y, width, height, **kwargs):
        item = PosterLayer(id=f'layer-{len(layers)+1}', type=type, name=name,
                           x=x, y=y, width=width, height=height, z=len(layers), **kwargs)
        if type == 'text':
            floor = 10 if kwargs.get('role') == 'watermark' else (14 if kwargs.get('role') == 'disclosure' else 18)
            _fit(item, minimum=floor)
        layers.append(item)
        return item
    landscape = w / h > 1.25
    if landscape:
        text_box, product_box = (m, h*.15, w*.43, h*.20), (w*.54, h*.14, w*.40, h*.65)
        subtitle_box = (m, h*.365, w*.43, h*.11)
        feature_y, feature_width, feature_total_height = h*.50, w*.43, h*.32
    else:
        headline_width = w*.80 if brief.direction == 'editorial' else w-2*m
        text_box, product_box = (m, h*.12, headline_width, h*.16), (m, h*.40, w-2*m, h*.26)
        subtitle_box = (m, h*.29, w-2*m, h*.085)
        feature_y, feature_width, feature_total_height = h*.685, w-2*m, h*.155
    background = '#102E26' if brief.direction == 'bold' else brief.background_color
    foreground = '#FFFFFF' if brief.direction == 'bold' else dark
    assets = {}
    source_assets = result.get('assets', [])
    selected = {image.get('asset_id') for image in result.get('images', [])}
    candidates = [asset for asset in source_assets if asset.get('product') == task.get('product')
                  and asset.get('kind', 'product') not in ('background', 'generated_background')]
    # A marketing article's lifestyle cover is not automatically a suitable
    # foreground for a commercial product poster. Prefer observed product views.
    chosen = next((a for a in candidates if a.get('visual_role') == 'product_overview'
                   and a.get('semantic_basis') == 'pixel_review_sha256'), None)
    if not chosen:
        chosen = next((a for a in candidates if a.get('visual_role') == 'product_overview'), None)
    if not chosen:
        chosen = next((a for a in candidates if a.get('id') in selected), None)
    if not chosen and candidates:
        chosen = candidates[0]
    for asset in source_assets:
        aid = str(asset.get('id', ''))
        if not aid:
            continue
        # Deliberately exclude source URLs and local paths from editable exports.
        assets[aid] = {key: asset.get(key, '') for key in
                       ('source_id', 'provenance', 'verified', 'title', 'sha256', 'product')}
        if asset.get('data_url'):
            assets[aid]['data_url'] = asset['data_url']
    if brief.background_asset_id:
        layer('image', 'AI生成背景（非产品实拍）', 0, 0, w, h,
              asset_id=brief.background_asset_id, role='generated_background', locked=True)
    if brief.background_asset_id:
        # Unknown generated pixels must never determine text contrast. Keep
        # narrow outer margins and the entire product area open to the image.
        pad_x, pad_y = w*.015, h*.008
        plate_fill = '#102E26' if brief.direction == 'bold' else '#F3F0E8'
        def plate(name, x, y, width, height):
            layer('shape', name, x, y, width, height, fill=plate_fill,
                  opacity=.96, radius=12*scale, locked=True, role='contrast_backplate')
        plate('品牌可读性背板', m-pad_x, h*.025, w-2*m+2*pad_x, h*.065)
        title_bottom = subtitle_box[1]+subtitle_box[3]
        plate('标题与副标题可读性背板', m-pad_x, text_box[1]-pad_y,
              subtitle_box[2]+2*pad_x, title_bottom-text_box[1]+2*pad_y)
        if result.get('blocks'):
            plate('卖点可读性背板', m-pad_x, feature_y-pad_y,
                  feature_width+2*pad_x, feature_total_height+2*pad_y)
        plate('限定条件与状态可读性背板', m-pad_x, h*.91,
              w-2*m+2*pad_x, h*.088)
    # Graphic framing is editable, separate from photography and actual text.
    if brief.direction == 'bold':
        layer('shape', '视觉强调色块', w*.76, 0, w*.24, h*.10, fill=accent)
    elif brief.direction == 'editorial':
        layer('shape', '编辑式分隔线', m, h*.092, w-2*m, max(2, h*.002), fill=accent)
    else:
        layer('shape', '标题强调条', m, h*.095, w*.09, max(4, h*.004), fill=accent, radius=2)
    layer('text', '品牌与用途', m, h*.035, w-2*m, h*.045,
          text=brief.brand_name or task.get('product', ''), font_size=max(16, 24*scale),
          color=foreground, font_weight=600, role='brand')
    layer('text', '主标题', *text_box, text=brief.headline or result.get('title') or '找到适合你的工作方式',
          font_size=min(96, 76*scale), font_weight=800, color=foreground, role='headline')
    layer('text', '场景副标题', *subtitle_box,
          text=brief.subtitle or result.get('intro') or '',
          font_size=max(16, 25*scale), color=foreground, role='subtitle')
    if chosen:
        layer('image', '真实产品图（锁定）', *product_box, asset_id=chosen['id'], role='product', locked=True,
              source_ids=[chosen.get('source_id', '')])
    else:
        layer('shape', '缺少真实产品素材', *product_box, fill='#DCE4DF', radius=20)
    blocks = result.get('blocks', [])[:3]
    feature_step = feature_total_height / max(1,len(blocks))
    feature_height = feature_step - h*.008
    for idx, block in enumerate(blocks):
        layer('text', f'产品卖点 {idx+1}', m, feature_y + idx*feature_step,
              feature_width, feature_height, text=block.get('text', ''), font_size=max(16, 27*scale),
              color=foreground, fact_ids=block.get('fact_ids', []), role='benefit')
    disclosures = list(dict.fromkeys(d for block in result.get('blocks', [])
                                     for d in block.get('disclosures', []) if d))
    layer('shape', '行动按钮底色', m, h*.858, w-2*m, h*.047, fill=accent, radius=10*scale)
    layer('text', '行动引导', m+w*.018, h*.861, w-2*m-w*.036, h*.04,
          text=brief.cta or result.get('cta') or '预约到店，验证你的使用场景',
          font_size=max(16, 26*scale), color='#FFFFFF', font_weight=700, align='center', role='cta')
    footer = '；'.join(disclosures)
    if footer:
        layer('text', '事实限定与适用条件', m, h*.915, w-2*m, h*.052,
              text=footer, font_size=max(14, 17*scale), color=foreground, role='disclosure', locked=True,
              fact_ids=list(dict.fromkeys(f for b in result.get('blocks', []) for f in b.get('fact_ids', []))))
    layer('text', '草稿状态水印', m, h*.973, w-2*m, h*.023,
          text='' if result.get('status') == 'ready' else '设计草稿 · 产品素材与文案需复核后发布', font_size=max(10, 14*scale),
          color=foreground, align='right', role='watermark', locked=True)
    doc = PosterDocument(width=w, height=h, canvas=PosterCanvas(width=w, height=h, background=background),
                         brief=brief, layers=layers, assets=assets, source_run_id=result.get('run_id', ''))
    doc.audit = [dict(item) for item in result.get('audit', []) if item.get('severity') in ('error', 'warning')] + audit_document(doc)
    if chosen and not chosen.get('verified'):
        doc.audit.append({'severity':'warning','category':'product_provenance','message':'产品素材尚未完成来源核验，当前为设计草稿'})
    if brief.background_mode == 'generate' and not brief.background_asset_id:
        doc.audit.append({'severity':'warning','category':'background','message':'生成背景尚未提供；当前使用纯色背景'})
    if not chosen:
        doc.audit.append({'severity':'error','category':'product','message':'未找到与准确型号匹配的真实产品素材，不能作为发布成品'})
    doc.status = 'blocked' if any(i['severity']=='error' for i in doc.audit) else 'draft'
    return doc


def _raster_data(value) -> str | None:
    import io
    from PIL import Image, UnidentifiedImageError

    if isinstance(value, str):
        if len(value) > 32_000_100:
            return None
        match = re.fullmatch(r'data:image/(png|jpeg|webp|gif);base64,([A-Za-z0-9+/=\r\n]+)', value)
        if not match:
            return None
        try:
            payload = base64.b64decode(match.group(2), validate=True)
        except (ValueError, TypeError):
            return None
    elif isinstance(value, bytes):
        payload = value
    else:
        return None
    if len(payload) > 24_000_000:
        return None
    if payload.startswith(b'\x89PNG\r\n\x1a\n'):
        kind = 'png'
    elif payload.startswith(b'\xff\xd8\xff'):
        kind = 'jpeg'
    elif payload.startswith((b'GIF87a', b'GIF89a')):
        kind = 'gif'
    elif payload[:4] == b'RIFF' and payload[8:12] == b'WEBP':
        kind = 'webp'
    else:
        return None
    # Magic bytes are only a format hint. Verify the complete raster and force
    # first-frame decoding before allowing a browser to embed those bytes.
    try:
        with Image.open(io.BytesIO(payload)) as image:
            width, height = image.size
            if not (0 < width <= 16384 and 0 < height <= 16384 and width * height <= 40_000_000):
                return None
            if image.format not in ('PNG', 'JPEG', 'WEBP', 'GIF'):
                return None
            image.verify()
        with Image.open(io.BytesIO(payload)) as image:
            image.load()
    except (UnidentifiedImageError, OSError, ValueError, Image.DecompressionBombError):
        return None
    return f'data:image/{kind};base64,' + base64.b64encode(payload).decode('ascii')


def _xml(value):
    return html.escape(''.join(c for c in str(value) if ord(c)>=32 or c in '\n\t'), quote=True)


def render_svg(document: PosterDocument | dict, asset_data: Mapping | Callable | None = None) -> str:
    doc = document if isinstance(document, PosterDocument) else PosterDocument.model_validate(document)
    if not COLOR.fullmatch(doc.canvas.background):
        raise ValueError('画布背景颜色无效')
    svg = [f'<svg xmlns="http://www.w3.org/2000/svg" width="{doc.width}" height="{doc.height}" viewBox="0 0 {doc.width} {doc.height}" role="img" aria-label="可编辑消费电子海报">',
           f'<rect width="100%" height="100%" fill="{doc.canvas.background}"/>']
    fonts={'sans':'Noto Sans CJK SC, Microsoft YaHei, sans-serif',
           'serif':'Noto Serif CJK SC, SimSun, Georgia, serif',
           'mono':'Consolas, Noto Sans Mono CJK SC, monospace',
           'display':'Microsoft YaHei, Noto Sans SC, Arial Black, sans-serif'}
    for index,layer in enumerate(sorted(doc.layers, key=lambda item: item.z)):
        if not layer.visible:
            continue
        x,y,w,h = layer.x,layer.y,layer.width,layer.height
        effects = ''
        image_filter = ''
        if layer.rotation:
            effects += f' transform="rotate({layer.rotation} {x+w/2} {y+h/2})"'
        if layer.shadow_opacity:
            shadow_id = f'shadow-{index}'
            svg.append(f'<defs><filter id="{shadow_id}" x="-100%" y="-100%" width="300%" height="300%"><feDropShadow dx="{layer.shadow_dx}" dy="{layer.shadow_dy}" stdDeviation="{layer.shadow_blur}" flood-color="{layer.shadow_color}" flood-opacity="{layer.shadow_opacity}"/></filter></defs>')
            # Keep legacy group-level shadows when no separate ground plane
            # exists. With a contact ellipse, filter only the product image so
            # the ground shadow is not drop-shadowed a second time.
            if layer.type == 'image' and layer.contact_shadow is not None and layer.contact_shadow.opacity:
                image_filter = f' filter="url(#{shadow_id})"'
            else:
                effects += f' filter="url(#{shadow_id})"'
        svg.append(f'<g id="{_xml(layer.id)}"{effects}><title>{_xml(layer.name)}</title>')
        if layer.type == 'shape':
            fill=layer.fill
            if layer.fill_style!='solid':
                gid=f'paint-{index}'
                tag='linearGradient' if layer.fill_style=='linear' else 'radialGradient'
                svg.append(f'<defs><{tag} id="{gid}"><stop offset="0%" stop-color="{layer.fill}"/><stop offset="100%" stop-color="{layer.gradient_end}"/></{tag}></defs>')
                fill=f'url(#{gid})'
            attrs=f'fill="{fill}" opacity="{layer.opacity}" stroke="{layer.stroke}" stroke-width="{layer.stroke_width}"'
            if layer.geometry=='ellipse':
                svg.append(f'<ellipse cx="{x+w/2}" cy="{y+h/2}" rx="{w/2}" ry="{h/2}" {attrs}/>')
            elif layer.geometry=='line':
                svg.append(f'<line x1="{x}" y1="{y}" x2="{x+w}" y2="{y+h}" {attrs}/>')
            elif layer.geometry=='path':
                svg.append(f'<svg x="{x}" y="{y}" width="{w}" height="{h}" viewBox="0 0 100 100" preserveAspectRatio="none"><path d="{_xml(layer.path_d)}" {attrs}/></svg>')
            else:
                svg.append(f'<rect x="{x}" y="{y}" width="{w}" height="{h}" rx="{layer.radius}" {attrs}/>')
        elif layer.type == 'image':
            if layer.contact_shadow is not None and layer.contact_shadow.opacity:
                ground = layer.contact_shadow
                ground_id = f'contact-shadow-{index}'
                svg.append(f'<defs><filter id="{ground_id}" x="-100%" y="-300%" width="300%" height="700%"><feGaussianBlur stdDeviation="{ground.blur}"/></filter></defs>')
                svg.append(f'<ellipse data-role="contact-shadow" cx="{x+w*ground.center_x}" cy="{y+h*ground.center_y}" rx="{w*ground.width/2}" ry="{h*ground.height/2}" fill="{ground.color}" opacity="{ground.opacity*layer.opacity}" filter="url(#{ground_id})"/>')
            supplied = asset_data(layer.asset_id) if callable(asset_data) else (asset_data or {}).get(layer.asset_id)
            embedded = _raster_data(supplied or doc.assets.get(layer.asset_id, {}).get('data_url'))
            if embedded:
                fit = 'xMidYMid slice' if layer.role == 'generated_background' else 'xMidYMid meet'
                svg.append(f'<image x="{x}" y="{y}" width="{w}" height="{h}" href="{embedded}" preserveAspectRatio="{fit}" opacity="{layer.opacity}"{image_filter}/>')
            else:
                svg.append(f'<rect x="{x}" y="{y}" width="{w}" height="{h}" fill="#DCE4DF"/>')
                svg.append(f'<text x="{x+w/2}" y="{y+h/2}" text-anchor="middle" font-size="20" fill="#172E27">真实素材尚未载入</text>')
        else:
            anchor = {'left':'start','center':'middle','right':'end'}[layer.align]
            tx = x + {'left':0,'center':w/2,'right':w}[layer.align]
            svg.append(f'<text fill="{layer.color}" font-family="{fonts[layer.font_family]}" font-size="{layer.font_size}" font-weight="{layer.font_weight}" letter-spacing="{layer.letter_spacing}" text-anchor="{anchor}" xml:space="preserve">')
            for line_index, line in enumerate(wrap_text(layer.text,w,layer.font_size,layer.letter_spacing)):
                svg.append(f'<tspan x="{tx}" y="{y+layer.font_size*(1+line_index*layer.line_height)}">{_xml(line)}</tspan>')
            svg.append('</text>')
        svg.append('</g>')
    svg.append('</svg>')
    return ''.join(svg)


def apply_edits(document: PosterDocument | dict, edits: list[dict]) -> PosterDocument:
    """Apply explicit local patches; never accept arbitrary SVG/HTML or asset URLs."""
    doc = (document if isinstance(document, PosterDocument) else PosterDocument.model_validate(document)).model_copy(deep=True)
    if len(edits) > 100:
        raise ValueError('单次最多修改100个图层')
    allowed = {'x','y','width','height','text','font_size','font_weight','font_family','letter_spacing','line_height','color','align','fill','radius','visible','z'}
    by_id = {layer.id: layer for layer in doc.layers}
    for edit in edits:
        layer_id = edit.get('id', edit.get('layer_id'))
        if layer_id not in by_id:
            raise ValueError('图层不存在')
        current = by_id[layer_id]
        if current.locked:
            raise ValueError('锁定图层不可修改；产品原图、限定条件和草稿状态须保留')
        changes = edit.get('changes', {k:v for k,v in edit.items() if k not in ('id','layer_id')})
        if not isinstance(changes,dict) or set(changes)-allowed:
            raise ValueError('包含不支持的图层修改字段')
        updated = PosterLayer.model_validate({**current.model_dump(), **changes})
        doc.layers[doc.layers.index(current)] = updated
        by_id[layer_id] = updated
    doc.version += 1
    layout_categories = {'layer','bounds','product','overflow','legibility','canvas','overlap'}
    doc.audit = [item for item in doc.audit if item.get('category') not in layout_categories] + audit_document(doc)
    if any(item['severity']=='error' for item in doc.audit):
        raise ValueError('；'.join(item['message'] for item in doc.audit if item['severity']=='error'))
    return doc
