from __future__ import annotations

from typing import Literal
from uuid import uuid4
from pydantic import BaseModel, Field, model_validator


class TaskInput(BaseModel):
    product: str = ''
    audience: str = ''
    scene: str = ''
    platform: str = Field(default='xiaohongshu', min_length=1, max_length=64,
                          pattern=r'^[a-z][a-z0-9_-]*$')
    stage: str = 'consideration'
    goal: str = ''
    requirements: str = ''
    creative_connector: str = Field(default='auto', max_length=100)
    creative_style: str = Field(default='auto', max_length=100)
    mode: Literal['local', 'demo', 'live'] = 'local'
    ecosystem_products: list[str] = Field(default_factory=list, max_length=4)
    media_ids: list[str] = Field(default_factory=list, max_length=12)
    store_id: str = Field(default='', max_length=100)
    assignment_id: str = Field(default='', max_length=160)

    @model_validator(mode='before')
    @classmethod
    def platform_stage_default(cls, value):
        # A Moments follower/contact is not necessarily considering a purchase.
        # Preserve explicit stages and the other platforms' existing default.
        if isinstance(value, dict) and value.get('platform') == 'moments' and not value.get('stage'):
            return {**value, 'stage': 'unknown'}
        return value


class Fact(BaseModel):
    id: str
    product: str
    label: str
    value: str
    quote: str
    source_id: str
    qualifiers: list[str] = Field(default_factory=list)
    system: str = ''
    # Internal evidence applicability, not a public advertising disclosure.
    sku_scope: str = ''


class Asset(BaseModel):
    id: str
    product: str
    url: str
    source_id: str
    title: str = ''
    original_title: str = ''
    kind: str = 'product'
    local_path: str = ''
    provenance: str = 'ima_direct'
    verified: bool = False
    semantic_tags: list[str] = Field(default_factory=list)
    visual_role: str = 'unknown'
    semantic_basis: str = 'title_only'
    visual_group: str = ''
    claim_scope: str = ''
    sha256: str = ''
    captured_at: str = ''


class Source(BaseModel):
    id: str
    title: str
    url: str = ''
    provider: str = 'ima'
    excerpt: str = ''
    sha256: str = ''
    captured_at: str = ''


class EvidenceBundle(BaseModel):
    product: str
    system: str = ''
    facts: list[Fact] = Field(default_factory=list)
    assets: list[Asset] = Field(default_factory=list)
    sources: list[Source] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    conflicts: list[str] = Field(default_factory=list)
    verified_live: bool = False
    verified_local: bool = False
    user_materials: list[dict] = Field(default_factory=list)


class ContentBlock(BaseModel):
    text: str
    fact_ids: list[str] = Field(default_factory=list)
    disclosures: list[str] = Field(default_factory=list)


def creative_review_blocks(plan: dict) -> list[ContentBlock]:
    """Map publishable card copy to the same evidence boundary as body blocks.

    Keep the original card fields intact for rendering. Layout/shot proposals
    remain proposals, and the full plan is also sent to semantic verification.
    """
    blocks = []
    for card in plan.get('cards', []):
        if not isinstance(card, dict):
            continue
        text = '\n'.join(str(card.get(key, '')) for key in ('headline', 'body', 'caption', 'overlay_text') if card.get(key))
        blocks.append(ContentBlock(text=text, fact_ids=card.get('fact_ids', []),
                                   disclosures=card.get('disclosures', [])))
    # Test headlines are selectable public copy, not a safe place to invent a
    # new price or feature. Their schema has no citations: keep them claim-free.
    for variant in plan.get('test_variants', []):
        if isinstance(variant, dict):
            blocks.append(ContentBlock(text='\n'.join(str(variant.get(key,'')) for key in ('title','cover_copy'))))
    return blocks


class ImagePlacement(BaseModel):
    asset_id: str
    caption: str
    position: str
    block_index: int | None = Field(default=None, ge=0)
    role: str = 'product'
    reason: str = ''
    supported_fact_ids: list[str] = Field(default_factory=list)


class AuditItem(BaseModel):
    severity: Literal['info', 'warning', 'error']
    category: str
    message: str


class TraceItem(BaseModel):
    stage: str
    detail: str
    duration_ms: int = 0


class RunResult(BaseModel):
    run_id: str = Field(default_factory=lambda: uuid4().hex)
    status: Literal['ready', 'needs_input', 'blocked', 'error', 'draft'] = 'draft'
    mode: Literal['local', 'demo', 'live'] = 'local'
    model_used: str = ''
    questions: list[str] = Field(default_factory=list)
    strategy: dict = Field(default_factory=dict)
    content_plan: dict = Field(default_factory=dict)
    creative_plan: dict = Field(default_factory=dict)
    editorial_review: list[AuditItem] = Field(default_factory=list)
    sales_plan: dict = Field(default_factory=dict)
    title: str = ''
    intro: str = ''
    blocks: list[ContentBlock] = Field(default_factory=list)
    cta: str = ''
    tags: list[str] = Field(default_factory=list)
    images: list[ImagePlacement] = Field(default_factory=list)
    facts: list[Fact] = Field(default_factory=list)
    assets: list[Asset] = Field(default_factory=list)
    sources: list[Source] = Field(default_factory=list)
    audit: list[AuditItem] = Field(default_factory=list)
    trace: list[TraceItem] = Field(default_factory=list)
