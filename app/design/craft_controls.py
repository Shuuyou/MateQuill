"""Apply one authorized native control edit to an existing source-bound plan.

This helper never edits raster bytes, unlocks a product layer or calls a model.
The caller retains authorization, compilation and actual final-render review.
"""
from __future__ import annotations

from copy import deepcopy

from pydantic import BaseModel, ConfigDict, Field, field_validator

from .craft import REGION_CONTROLS, build_craft_revision, check_craft_revision
from .models import DesignPlan, LayoutRegion


class CraftEditInput(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True, allow_inf_nan=False)
    page_id: str = Field(min_length=1, max_length=80)
    region_id: str = Field(min_length=1, max_length=80)
    values: dict[str, object] = Field(min_length=1, max_length=len(REGION_CONTROLS))
    goal: str = Field(min_length=1, max_length=1200)

    @field_validator('page_id', 'region_id', 'goal')
    @classmethod
    def meaningful_string(cls, value):
        if not value.strip():
            raise ValueError('craft_edit_nonempty_string_required')
        return value

    @field_validator('values')
    @classmethod
    def supported_controls(cls, value):
        if not set(value) <= set(REGION_CONTROLS):
            raise ValueError('craft_revision_unsupported_control')
        return value


def apply_craft_controls(parent_plan, contract, *, page_id, region_id, values, goal):
    """Return a validated new plan and frozen work order; mutate no parent input.

    ``contract`` and ``parent_plan`` must be server-owned authorized objects.
    The order carries its baseline for verification and a separate small summary
    for persistence. Plan provenance is untouched, preserving full-scope checks.
    """
    request = CraftEditInput.model_validate({'page_id': page_id, 'region_id': region_id,
        'values': values, 'goal': goal}, strict=True)
    parent = (parent_plan if isinstance(parent_plan, DesignPlan)
              else DesignPlan.model_validate(parent_plan))
    baseline = parent.model_dump(mode='python')
    order = build_craft_revision(contract, baseline, page_id=request.page_id,
        region_id=request.region_id, goal=request.goal, controls=list(request.values))
    candidate = deepcopy(baseline)
    selected = next(row for row in candidate['concepts']
                    if row['id'] == candidate['selected_concept_id'])
    if selected.get('pages'):
        target = next(region for page in selected['pages'] if page['id'] == request.page_id
                      for region in page['composition'] if region.get('id') == request.region_id)
    else:
        target = next(region for region in selected['composition']
                      if region.get('id') == request.region_id)
    target.update(deepcopy(request.values))
    # Strict region validation rejects numeric strings/bools, NaN, unsupported
    # enum values, non-image contact shadows and unsafe nested shadow objects.
    validated_region = LayoutRegion.model_validate(target, strict=True)
    target.clear()
    target.update(validated_region.model_dump(mode='python'))
    revised = DesignPlan.model_validate(candidate, strict=True)
    scope = check_craft_revision(order, revised)
    if not scope.get('within_scope'):
        raise ValueError('craft_revision_changed_locked_content')
    order['summary'] = {'contract_id': order['contract_id'], 'page_id': request.page_id,
        'region_id': request.region_id, 'goal': request.goal,
        'controls': list(request.values), 'base_plan_digest': order['base_plan_digest'],
        'requires_actual_pixel_review': True}
    return revised, order
