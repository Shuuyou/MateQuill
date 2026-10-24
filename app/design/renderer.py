"""Render font outlines for portable pixel fidelity; native text stays in document.

The SVG retains accessible original strings on each text group. Actual editing
uses DesignDocument/PosterDocument text layers rather than reverse engineering
flattened typography. No network requests or filesystem assets are accepted.
"""
from __future__ import annotations
import html
import base64
import hashlib
import io
from PIL import Image, UnidentifiedImageError
from ..poster import PosterDocument,render_svg
from .typography import semantic_wrap,measure
from .font_resolver import resolve_glyph


def try_full_frame_png(document, asset_data=None) -> bytes | None:
    """Preserve an already finished opaque PNG when composition is an identity.

    This is byte reuse, not a rendering or review success. Any actual layout,
    transparency, resizing, or extra layer continues through the SVG renderer.
    """
    try:
        doc = document if isinstance(document, PosterDocument) else PosterDocument.model_validate(document)
        if len(doc.layers) != 1 or (doc.width, doc.height) != (doc.canvas.width, doc.canvas.height):
            return None
        if not (320 <= doc.width <= 4096 and 320 <= doc.height <= 4096 and doc.width * doc.height <= 12_000_000):
            return None
        layer = doc.layers[0]
        if (not layer.visible or layer.type != 'image' or layer.role != 'generated_design' or not layer.locked
                or layer.x != 0 or layer.y != 0 or layer.width != doc.width or layer.height != doc.height
                or layer.opacity != 1 or layer.radius != 0 or layer.stroke_width != 0 or layer.path_d or layer.text
                or layer.shadow_opacity != 0 or layer.contact_shadow is not None or not layer.asset_id):
            return None
        # Transformed or shadowed layers require the actual compositor.
        transforms = ('transform', 'rotation', 'rotate', 'crop', 'crop_box', 'flip_x', 'flip_y')
        if any(getattr(layer, key, None) for key in transforms):
            return None
        metadata = doc.assets.get(layer.asset_id, {})
        if any(metadata.get(key) for key in transforms):
            return None
        supplied = asset_data(layer.asset_id) if callable(asset_data) else (asset_data or {}).get(layer.asset_id)
        value = supplied or metadata.get('data_url')
        if isinstance(value, bytes):
            raw = value
        elif isinstance(value, str) and value.startswith('data:image/png;base64,') and len(value) <= 32_000_100:
            raw = base64.b64decode(value.split(',', 1)[1], validate=True)
        else:
            return None
        if len(raw) > 24_000_000 or not raw.startswith(b'\x89PNG\r\n\x1a\n'):
            return None
        if metadata.get('sha256') and hashlib.sha256(raw).hexdigest() != metadata['sha256']:
            return None
        with Image.open(io.BytesIO(raw)) as image:
            if image.format != 'PNG' or image.size != (doc.width, doc.height) or getattr(image, 'n_frames', 1) != 1:
                return None
            image.verify()
        with Image.open(io.BytesIO(raw)) as image:
            image.load()
            if image.getexif().get(274, 1) != 1:
                return None
            if image.convert('RGBA').getchannel('A').getextrema() != (255, 255):
                return None
        return raw
    except (ValueError, TypeError, KeyError, SyntaxError, EOFError, UnidentifiedImageError, OSError, Image.DecompressionBombError):
        return None

def render_design_svg(document,asset_data=None):
    from xml.etree import ElementTree as ET
    doc=document if isinstance(document,PosterDocument) else PosterDocument.model_validate(document)
    base=doc.model_copy(deep=True);base.layers=[l for l in base.layers if l.type!='text']
    for layer in base.layers:
        if layer.type=='image' and layer.role in ('photograph', 'generated_design'):
            layer.role='generated_background'
    svg=render_svg(base,asset_data)
    ET.register_namespace('', 'http://www.w3.org/2000/svg')
    root=ET.fromstring(svg)
    groups={child.get('id'):ET.tostring(child,encoding='unicode') for child in root
            if child.tag.rsplit('}',1)[-1]=='g'}
    # Preserve the background and effect definitions, then paint all layers in
    # their actual shared z order. Type can genuinely sit behind a product.
    parts=[svg[:svg.index('>')+1]]
    parts.extend(ET.tostring(child,encoding='unicode') for child in root
                 if child.tag.rsplit('}',1)[-1]!='g')
    for layer in sorted(doc.layers,key=lambda l:l.z):
        if not layer.visible:continue
        if layer.type!='text':
            parts.append(groups.get(layer.id,''));continue
        effects=''
        if layer.rotation:
            effects+=f' transform="rotate({layer.rotation} {layer.x+layer.width/2} {layer.y+layer.height/2})"'
        if layer.shadow_opacity:
            effect_id='type-shadow-'+hashlib.sha256(layer.id.encode()).hexdigest()[:16]
            parts.append(f'<defs><filter id="{effect_id}" x="-100%" y="-100%" width="300%" height="300%"><feDropShadow dx="{layer.shadow_dx}" dy="{layer.shadow_dy}" stdDeviation="{layer.shadow_blur}" flood-color="{layer.shadow_color}" flood-opacity="{layer.shadow_opacity}"/></filter></defs>')
            effects+=f' filter="url(#{effect_id})"'
        parts.append(f'<g id="{html.escape(layer.id,quote=True)}" role="text" aria-label="{html.escape(layer.text,quote=True)}" fill="{layer.color}"{effects}><title>{html.escape(layer.text)}</title>')
        lines=semantic_wrap(layer.text,layer.width,layer.font_size,layer.letter_spacing,layer.font_family,layer.font_weight)
        for n,line in enumerate(lines):
            length=measure(line,layer.font_size,layer.letter_spacing,layer.font_family,layer.font_weight)
            x=layer.x+({'left':0,'center':(layer.width-length)/2,'right':layer.width-length}[layer.align])
            baseline=layer.y+layer.font_size*(1+n*layer.line_height)
            for char in line:
                glyph=resolve_glyph(char,layer.font_family,layer.font_weight)
                scale=layer.font_size/glyph.units_per_em
                if glyph.path:parts.append(f'<path d="{glyph.path}" transform="translate({x:.3f} {baseline:.3f}) scale({scale:.8f} {-scale:.8f})"/>')
                x+=glyph.advance*scale+layer.letter_spacing
        parts.append('</g>')
    return ''.join(parts)+'</svg>'
