"""Measured Chinese typography with a redistributable, colocated font."""
from __future__ import annotations
import re
from io import BytesIO
from functools import lru_cache
from pathlib import Path
from PIL import ImageFont
from .font_resolver import resolve_glyph

FONT_PATH = Path(__file__).parent / 'fonts' / 'NotoSansCJKsc-Regular.otf'
OPEN = '（【《「『〔〈“‘'
CLOSE = '，。！？；：、）》】」』〕〉”’%％'
PHRASES = ('多屏协同','下一页','写成','想法','华为','鸿蒙','到店','体验','耳机','平板','手机','电脑','无线','新品','随身','通勤','创作','学习','设计','日常','门店','带上','打开')
TOKEN_PATTERN = r'[A-Za-z0-9]+(?:[ .+×\-/][A-Za-z0-9]+)*|'+'|'.join(map(re.escape,sorted(PHRASES,key=len,reverse=True)))+r'|[^A-Za-z0-9]'

@lru_cache(maxsize=8)
def font(size: int):
    if not FONT_PATH.is_file():
        raise RuntimeError('design_font_missing')
    return ImageFont.truetype(BytesIO(FONT_PATH.read_bytes()), max(8, size))

@lru_cache(maxsize=8192)
def measure(text: str, size: float, tracking: float = 0, font_family: str='sans', font_weight: int=400) -> float:
    glyphs=[resolve_glyph(char,font_family,font_weight) for char in text]
    return sum(g.advance*size/g.units_per_em for g in glyphs) + max(0,len(text)-1)*tracking

def semantic_wrap(text: str, width: float, size: float, tracking: float = 0, font_family: str='sans', font_weight: int=400,
                  preferred_breaks=None) -> list[str]:
    """Dynamic line breaking: atomic Latin model tokens, Chinese punctuation rules.

    Minimize raggedness plus unnatural phrase cuts; preserve all source characters.
    An indivisible model token can exceed the box so the caller must reduce size.
    """
    preferred={offset for offset in (preferred_breaks or ())
               if isinstance(offset,int) and not isinstance(offset,bool) and 0<offset<len(text)}
    result=[];paragraph_offset=0
    for para in text.split('\n'):
        tokens=re.findall(TOKEN_PATTERN, para)
        n=len(tokens)
        if not n:
            result.append('');paragraph_offset+=len(para)+1;continue
        offsets=[paragraph_offset]
        for token in tokens:offsets.append(offsets[-1]+len(token))
        # Preferences never split an atomic model token or override punctuation.
        preferred_here=preferred.intersection(offsets[1:-1])
        best=[float('inf')]*(n+1); nxt=[n]*(n+1); best[n]=0
        for i in range(n-1,-1,-1):
            for j in range(i+1,n+1):
                line=''.join(tokens[i:j]); length=measure(line,size,tracking,font_family,font_weight)
                if length>width and j>i+1: break
                if j<n and (tokens[j][0] in CLOSE or tokens[j-1][-1] in OPEN): continue
                if j<n and tokens[j] in ('的','了','着','与','和','及'): penalty=width*width*.15
                else: penalty=0
                if j<n and tokens[j-1] in ('写成','带上','打开','让','把','为','用','从','在'):
                    penalty+=width*width*.35
                if j<n and len(line.strip())<2: penalty+=width*width
                if preferred_here and j<n and offsets[j] not in preferred_here:
                    penalty+=width*width*4
                cost=(max(0,width-length)**2)*(0.18 if j==n else 1)+penalty+best[j]
                if length>width: cost+=(length-width)**2*100
                if cost<best[i]: best[i],nxt[i]=cost,j
        i=0
        while i<n:
            j=max(i+1,nxt[i]);result.append(''.join(tokens[i:j]));i=j
        paragraph_offset+=len(para)+1
    return result

def fit_text(text: str, width: float, height: float, maximum: float, minimum: float=22, line_height: float=1.22, font_family: str='sans', font_weight: int=400, tracking: float=0,
             preferred_breaks=None):
    # Semantic units are preferred, not a new overflow condition. If no size in
    # the permitted range fits those boundaries, retain the measured fallback.
    choices=(preferred_breaks,None) if preferred_breaks else (None,)
    for preferences in choices:
        for size in range(round(maximum),round(minimum)-1,-1):
            lines=semantic_wrap(text,width,size,tracking=tracking,font_family=font_family,font_weight=font_weight,
                                preferred_breaks=preferences)
            if max((measure(line,size,tracking=tracking,font_family=font_family,font_weight=font_weight) for line in lines),default=0)<=width and len(lines)*size*line_height<=height:
                return '\n'.join(lines),size,True
    return '\n'.join(semantic_wrap(text,width,minimum,tracking=tracking,font_family=font_family,font_weight=font_weight,
                                   preferred_breaks=preferred_breaks)),minimum,False
