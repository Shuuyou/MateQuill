"""Exact variable-font metrics and outlines, shared by layout and export.

The coverage manifest is built from packaged OFL files, not browser or system
fonts. Weight uses the font's actual wght axis, never a thickening stroke.
"""
from __future__ import annotations
import json
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from fontTools.ttLib import TTFont
from fontTools.pens.svgPathPen import SVGPathPen

FONT_ROOT=Path(__file__).parent/'fonts'

@dataclass(frozen=True)
class DesignGlyph:
    path: str
    advance: float
    units_per_em: int
    family: str
    weight: int
    font_file: str
    fallback: bool=False

@lru_cache(maxsize=1)
def _coverage():
    manifest=json.loads((FONT_ROOT/'coverage.json').read_text(encoding='utf-8'))
    indexes={}
    for family,files in manifest.items():
        coverage={}
        # Latin-specific files first, followed by the disjoint CJK shards.
        for name in sorted(files,key=lambda s:('latin' not in s,s)):
            for codepoint in files[name]: coverage.setdefault(codepoint,name)
        indexes[family]=coverage
    return indexes

@lru_cache(maxsize=96)
def _face(relative):
    font=TTFont(FONT_ROOT/relative)
    return font,font.getBestCmap(),font['head'].unitsPerEm

@lru_cache(maxsize=192)
def _glyphset(relative,weight):
    face,_,_=_face(relative)
    variable='fvar' in face
    return face.getGlyphSet(location={'wght':weight} if variable else None)

@lru_cache(maxsize=16384)
def resolve_glyph(char:str,family:str='sans',weight:int=400)->DesignGlyph:
    family='serif' if family=='serif' else 'sans'
    weight=min(900,max(200,round(weight)))
    if char in ('\u200d','\ufe0e','\ufe0f'):
        return DesignGlyph('',0,1000,'emoji',weight,'NotoEmoji-wght.ttf',True)
    if char=='\t':
        space=resolve_glyph(' ',family,weight)
        return DesignGlyph('',space.advance*4,space.units_per_em,space.family,space.weight,space.font_file,space.fallback)
    filename=_coverage()[family].get(ord(char))
    fallback=filename is None
    if filename is not None: relative=f'{family}/{filename}'
    else: relative='NotoSansCJKsc-Regular.otf'
    face,cmap,upem=_face(relative)
    name=cmap.get(ord(char))
    if name is None:
        emoji,emoji_cmap,emoji_upem=_face('NotoEmoji-wght.ttf')
        if ord(char) in emoji_cmap:
            relative='NotoEmoji-wght.ttf';face,cmap,upem=emoji,emoji_cmap,emoji_upem
            name=cmap[ord(char)];family='emoji';fallback=True
    if name is None:
        # A missing Unicode glyph is explicit; silently drawing tofu would pass
        # bounds checks while producing an unusable marketing image.
        raise ValueError(f'design_glyph_unavailable:U+{ord(char):04X}')
    if 'fvar' in face:
        axis=next((a for a in face['fvar'].axes if a.axisTag=='wght'),None)
        if axis: weight=round(min(axis.maxValue,max(axis.minValue,weight)))
    glyphs=_glyphset(relative,weight)
    glyph=glyphs[name]
    pen=SVGPathPen(glyphs);glyph.draw(pen)
    actual_weight=weight if 'fvar' in face else 400
    return DesignGlyph(path=pen.getCommands(),advance=glyph.width,units_per_em=upem,
        family=family if not fallback or family=='emoji' else 'sans',weight=actual_weight,font_file=relative,fallback=fallback)

def font_provenance(text,family='sans',weight=400):
    glyphs=[resolve_glyph(char,family,weight) for char in text if char not in '\n\r\t']
    return {'requested_family':family,'requested_weight':weight,
        'font_files':sorted({g.font_file for g in glyphs}),
        'actual_weights':sorted({g.weight for g in glyphs}),
        'fallback_characters':sorted({char for char in text if char not in '\n\r\t' and resolve_glyph(char,family,weight).fallback}),
        'outline_method':'variable_font_glyphs'}
