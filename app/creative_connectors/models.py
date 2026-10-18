"""Validated, versioned contracts for editorial plans (no publishing capability)."""
from __future__ import annotations
from typing import Any, Literal
from pydantic import BaseModel, Field, ConfigDict, field_validator

class CreativeCard(BaseModel):
    model_config = ConfigDict(extra='ignore')
    id: str = Field(min_length=1, max_length=80)
    role: str = ''
    headline: str = ''
    body: str = ''
    layout: str = 'task_checklist'
    asset_ids: list[str] = Field(default_factory=list)
    fact_ids: list[str] = Field(default_factory=list)
    disclosures: list[str] = Field(default_factory=list)
    licenses: dict[str, Any] = Field(default_factory=dict)
    source_disclosures: dict[str, Any] = Field(default_factory=dict)
    truth_status: Literal['official_material','real_photo','illustrative','diagram','pending_capture','hypothetical'] = 'illustrative'
    visual_intent: str = ''
    block_index: int | None = Field(default=None, ge=0)

    @field_validator('truth_status', mode='before')
    @classmethod
    def canonical_truth_status(cls, value):
        aliases = {'官方素材':'official_material','产品素材':'official_material',
                   '真实实拍':'real_photo','用户提供':'real_photo',
                   '示意合成':'illustrative','设计提案':'illustrative','示意':'illustrative',
                   '图表整理':'diagram','文字卡':'diagram','文字型图片':'diagram',
                   '待拍摄':'pending_capture','条件设想':'hypothetical'}
        return aliases.get(value, value) if isinstance(value,str) else value

class PromisePayoff(BaseModel):
    promise: str = ''
    payoff_card_ids: list[str] = Field(default_factory=list)

class TestVariant(BaseModel):
    id: str = ''
    title: str = ''
    cover_copy: str = ''
    rationale: str = ''

class CreativePlan(BaseModel):
    model_config = ConfigDict(extra='ignore')
    connector_id: str = ''
    connector_version: str = ''
    platform: str = ''
    narrative_device: str = ''
    reader_tension: str = ''
    reader_gain: str = ''
    title_cover_relation: str = ''
    promise_payoff: list[PromisePayoff] = Field(default_factory=list)
    cards: list[CreativeCard] = Field(default_factory=list, max_length=12)
    test_variants: list[TestVariant] = Field(default_factory=list, max_length=6)
