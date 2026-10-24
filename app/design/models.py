"""Serializable native design intentions and editable deliveries."""
from typing import Literal
from pydantic import BaseModel, ConfigDict, Field, model_validator
from ..poster import PosterDocument, PosterLayer
from .connectors import PlatformDesignConnector
from .publication import PublicationStrategy

class LayoutRegion(BaseModel):
    model_config=ConfigDict(extra='forbid',allow_inf_nan=False)
    role: Literal['headline','image','body','brand','caption','kicker','shape','rule']
    id: str=Field(default='',max_length=80)
    z: int|None=Field(default=None,ge=-20,le=40)
    overlap_intent: Literal['none','layered_assets','type_behind_subject']='none'
    rotation: float=Field(default=0,ge=-180,le=180)
    shadow_color: str=Field(default='#000000',max_length=7)
    shadow_opacity: float=Field(default=0,ge=0,le=.6)
    shadow_blur: float=Field(default=0,ge=0,le=80)
    shadow_dx: float=Field(default=0,ge=-200,le=200)
    shadow_dy: float=Field(default=0,ge=-200,le=200)
    x: float=Field(ge=0,le=1)
    y: float=Field(ge=0,le=1)
    width: float=Field(gt=0,le=1)
    height: float=Field(gt=0,le=1)
    align: Literal['left','center','right']='left'
    image_kind: Literal['product','photo']='product'
    text_id: str=Field(default='',max_length=100)
    asset_id: str=Field(default='',max_length=100)
    font_scale: float|None=Field(default=None,ge=.012,le=1)
    font_weight: Literal[400,500,600,700,800]|None=None
    font_role: Literal['sans','serif','display']|None=None
    line_height: float=Field(default=1.15,ge=.95,le=1.8)
    letter_spacing: float=Field(default=0,ge=-6,le=30)
    color: str=''
    fill: str=''
    opacity: float=Field(default=1,ge=0,le=1)
    geometry: Literal['rect','ellipse','line','path']='rect'
    path_d: str=Field(default='',max_length=5000)
    fill_style: Literal['solid','linear','radial']='solid'
    gradient_end: str=Field(default='#FFFFFF',max_length=7)
    stroke: str=Field(default='',max_length=7)
    stroke_width: float=Field(default=0,ge=0,le=100)
    @model_validator(mode='after')
    def inside(self):
        if self.x+self.width>1.001 or self.y+self.height>1.001: raise ValueError('region_out_of_canvas')
        import re
        if self.color and not re.fullmatch(r'#[0-9A-Fa-f]{6}', self.color):
            raise ValueError('invalid_region_color')
        if self.fill == 'none':
            if self.role not in ('shape', 'rule') or self.fill_style != 'solid':
                raise ValueError('no_fill_requires_solid_shape_or_rule')
        elif self.fill and not re.fullmatch(r'#[0-9A-Fa-f]{6}', self.fill):
            raise ValueError('invalid_region_color')
        # Reuse the renderer's safe path alphabet and gradient/color contract.
        PosterLayer(id='region-validation',type='shape',x=0,y=0,width=1,height=1,
            geometry=self.geometry,path_d=self.path_d,fill_style=self.fill_style,
            fill=self.fill or '#FFFFFF',
            gradient_end=self.gradient_end,stroke=self.stroke or '#000000',stroke_width=self.stroke_width,
            rotation=self.rotation,shadow_color=self.shadow_color,shadow_opacity=self.shadow_opacity,
            shadow_blur=self.shadow_blur,shadow_dx=self.shadow_dx,shadow_dy=self.shadow_dy)
        if self.geometry=='path' and not self.path_d.strip():
            raise ValueError('missing_region_path')
        if self.overlap_intent=='type_behind_subject' and self.role!='headline':
            raise ValueError('type_behind_subject_is_for_artistic_headline_only')
        if (self.path_d or self.fill_style!='solid') and self.role not in ('shape','rule'):
            raise ValueError('decoration_fields_require_shape')
        return self

def _validate_regions(regions):
    for i,a in enumerate(regions):
        for b in regions[i+1:]:
            if a.role in ('shape','rule') or b.role in ('shape','rule'): continue
            if min(a.x+a.width,b.x+b.width)>max(a.x,b.x)+.002 and min(a.y+a.height,b.y+b.height)>max(a.y,b.y)+.002:
                # Real product cutouts may form a deliberate array. This only
                # licenses layout, never replacing or repainting product pixels.
                if a.role==b.role=='image' and (region_z(a)!=region_z(b) or a.overlap_intent=='layered_assets' or b.overlap_intent=='layered_assets'):
                    continue
                picture=next((r for r in (a,b) if r.role=='image'),None)
                text=next((r for r in (a,b) if r.role!='image'),None)
                if picture is not None and text is not None:
                    if picture.image_kind=='photo' and region_z(text)>region_z(picture):continue
                    if picture.image_kind!='photo' and text.role=='headline' and region_z(text)<region_z(picture):continue
                    if region_z(text)<=region_z(picture):
                        raise ValueError(f'functional_text_occlusion_requires_replan:{text.role}:{text.text_id}; '
                            'only artistic headline may sit behind a product; functional copy must remain fully readable, '
                            'and photo overlays must paint above the photo; keep approved wording and revise composition')
                raise ValueError(f'overlapping_content_regions: {a.role}:{a.text_id or a.asset_id or i}(z={region_z(a)}) vs '
                    f'{b.role}:{b.text_id or b.asset_id or regions.index(b)}(z={region_z(b)}); '
                    'keep text-text disjoint; only artistic headline may sit behind a product; '
                    'functional copy needs its own readable space; product arrays need distinct z')

def region_z(region):
    """One shared bounded stacking contract for validation and composition."""
    return region.z if region.z is not None else (0 if region.role in ('shape','rule') else 10 if region.role=='image' else 20)

class DesignPage(BaseModel):
    model_config=ConfigDict(extra='forbid')
    id: str=Field(max_length=80)
    role: str=Field(default='detail',max_length=60)
    title: str=Field(default='',max_length=100)
    visual_brief: str=Field(default='',max_length=2400)
    render_mode: Literal['native','visual_first']='native'
    composition: list[LayoutRegion]=Field(min_length=1,max_length=20)
    text_ids: list[str]=Field(default_factory=list,max_length=10)
    asset_id: str=Field(default='',max_length=100)
    @model_validator(mode='after')
    def coherent(self):
        _validate_regions(self.composition)
        return self

class ArtDirection(BaseModel):
    """An authored design rationale, distinct from factual product claims."""
    model_config=ConfigDict(extra='forbid')
    key_visual: str=Field(default='',max_length=600)
    typography: str=Field(default='',max_length=600)
    material_and_light: str=Field(default='',max_length=600)
    spatial_hierarchy: str=Field(default='',max_length=600)
    local_relevance: str=Field(default='',max_length=600)

class VisualDevice(BaseModel):
    model_config=ConfigDict(extra='forbid')
    mechanism: Literal['framing','comparison','sequence','typographic_structure','material_contrast','spatial_rhythm']
    purpose: str=Field(min_length=1,max_length=600)
    region_ids: list[str]=Field(min_length=1,max_length=20)

class DesignConcept(BaseModel):
    model_config=ConfigDict(extra='forbid')
    id: str=Field(max_length=80)
    name: str=Field(max_length=80)
    visual_thesis: str=Field(max_length=600)
    production_method: Literal['native_assembly','generated_editorial','source_photo','auto']='auto'
    art_direction: ArtDirection=Field(default_factory=ArtDirection)
    visual_devices: list[VisualDevice]=Field(default_factory=list,max_length=6)
    composition: list[LayoutRegion]=Field(min_length=1,max_length=20)
    text_ids: list[str]=Field(default_factory=list,max_length=10)
    asset_ids: list[str]=Field(default_factory=list,max_length=10)
    page_asset_ids: list[str]=Field(default_factory=list,max_length=10)
    pages: list[DesignPage]=Field(default_factory=list,max_length=9)
    background: str='#F2F0EB'
    foreground: str='#202520'
    accent: str='#CA5237'
    font_role: Literal['sans','serif','display']='sans'
    treatment: Literal['photo','product','editorial']='product'
    @model_validator(mode='after')
    def coherent(self):
        import re
        if not all(re.fullmatch(r'#[0-9A-Fa-f]{6}',x) for x in (self.background,self.foreground,self.accent)):
            raise ValueError('invalid_design_color')
        # Source-bound product arrays and type behind a subject require an
        # explicit stacking intent. Actual-pixel review still checks that key
        # product structure and the message survive the authored overlap.
        _validate_regions(self.composition)
        authored_regions=[r.id for page in self.pages for r in page.composition if r.id] or [r.id for r in self.composition if r.id]
        if len(authored_regions)!=len(set(authored_regions)):
            raise ValueError('duplicate_design_region_id')
        for device in self.visual_devices:
            if not set(device.region_ids)<=set(authored_regions):raise ValueError('unknown_visual_device_region')
        return self

class DesignBrief(BaseModel):
    platform: PlatformDesignConnector
    publication: PublicationStrategy|None=None
    product: str=''
    audience: str=''
    scene: str=''
    requirements: str=''
    publisher_kind: str='store'
    publisher_label: str=''
    instruction: str=''
    source_run_id: str=''
    texts: dict[str,dict]=Field(default_factory=dict)
    asset_ids: list[str]=Field(default_factory=list)
    brand_identity: dict=Field(default_factory=dict)
    store_context: dict=Field(default_factory=dict)

class DesignPlan(BaseModel):
    version: str='2.0'
    brief: DesignBrief
    concepts: list[DesignConcept]=Field(min_length=1,max_length=4)
    selected_concept_id: str
    provenance: dict=Field(default_factory=dict)
    @model_validator(mode='after')
    def selected(self):
        if self.selected_concept_id not in {c.id for c in self.concepts}: raise ValueError('unknown_concept')
        return self

class DesignArtboard(BaseModel):
    id: str
    role: str
    title: str
    document: PosterDocument
    concept_id: str
    typography: dict=Field(default_factory=dict)
    source_text_ids: list[str]=Field(default_factory=list)
    visual_brief: str=''
    render_mode: Literal['native','visual_first']='native'
    page_id: str=''

class DesignDocument(BaseModel):
    """A series is one versioned object; individual artboards remain editable."""
    id: str
    version: int=Field(default=1,ge=1)
    plan: DesignPlan
    artboards: list[DesignArtboard]
    source_run_id: str=''
    review: list[dict]=Field(default_factory=list)
    parent_version: int|None=None
