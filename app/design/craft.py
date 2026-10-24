"""Source-bound production briefs and local refinement targets, without a judge.

This module describes controls the existing compiler can use. Lighting and
material instructions are model intentions, never a claim of 3D render control.
It neither authorizes assets nor substitutes for fact or actual-pixel review.
"""
from __future__ import annotations

from copy import deepcopy
import hashlib
import json
import math


REGION_CONTROLS = (
    'x', 'y', 'width', 'height', 'z', 'rotation', 'align', 'font_scale',
    'font_weight', 'font_role', 'font_fit', 'line_height', 'letter_spacing', 'color',
    'opacity', 'shadow_color', 'shadow_opacity', 'shadow_blur', 'shadow_dx',
    'shadow_dy', 'contact_shadow', 'overlap_intent',
)
_TYPE = {'font_scale', 'font_weight', 'font_role', 'font_fit', 'line_height', 'letter_spacing', 'align'}
_LIGHT = {'shadow_color', 'shadow_opacity', 'shadow_blur', 'shadow_dx', 'shadow_dy', 'contact_shadow'}


def _data(value):
    if hasattr(value, 'model_dump'):
        return value.model_dump()
    return dict(value or {})


def _digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False,
                                    default=str).encode()).hexdigest()


def _asset_rows(assets):
    if isinstance(assets, dict):
        return {str(key): _data(row) for key, row in assets.items()}
    return {str(row.get('id')): row for item in (assets or [])
            if (row := _data(item)).get('id')}


def _dimensions(row):
    return row.get('width') or row.get('pixel_width') or 0, row.get('height') or row.get('pixel_height') or 0


def _capability(row):
    """Conservative content suitability; IDs and titles do not prove a view."""
    role = row.get('visual_role') or row.get('asset_kind') or row.get('kind') or ''
    width, height = _dimensions(row)
    screenshot = role in {'screenshot', 'ui_screenshot', 'web_screenshot', 'official_detail'}
    screenshot = screenshot or row.get('scope') == 'product_detail'
    screenshot = screenshot or (bool(width and height) and height / width > 3)
    bound = bool(row.get('sha256') and row.get('source_id'))
    reviewed = row.get('semantic_basis') == 'pixel_review_sha256'
    product = role in {'product_packshot', 'packshot'} and bound and reviewed and not screenshot
    photo = role in {'photograph', 'photo', 'scene_photo', 'store_photo'} and bound and reviewed
    views = list(row.get('visible_views') or []) if row.get('visible_views_basis') == 'pixel_review_sha256' else []
    return {'product_hero': product, 'scene_photo': photo, 'information_panel': True,
            'screenshot_or_detail': bool(screenshot), 'verified_visible_views': views,
            'width': width, 'height': height, 'source_bound': bound}


def build_craft_contract(brief, source=None, available_assets=None):
    """Build a serializable production work order from server-owned inputs.

    ``source.craft_requirements`` may carry ``communication_proposition``,
    ``reading_task``, ``required_view`` and ``visual_bridge`` authored upstream.
    Missing propositions remain an explicit planning task; no slogan is invented.
    ``available_assets`` must already be authorized by the caller.
    """
    brief, source = _data(brief), _data(source)
    platform = _data(brief.get('platform')) if not isinstance(brief.get('platform'), str) else {'platform': brief['platform']}
    result = _data(source.get('result') or source)
    requirements = _data(source.get('craft_requirements'))
    texts = deepcopy(brief.get('texts') or {})
    proposition = str(requirements.get('communication_proposition') or '')
    proposition_origin = 'authored_craft_requirement'
    if not proposition:
        candidates = [(key, _data(row)) for key, row in texts.items()
                      if 'headline' in key or key == 'title']
        if candidates:
            proposition = str(candidates[0][1].get('text') or '')
            proposition_origin = 'approved_text:' + candidates[0][0]
        elif result.get('title'):
            proposition = str(result['title'])
            proposition_origin = 'source_title_context_not_new_approved_copy'
    assets = _asset_rows(available_assets)
    product = str(brief.get('product') or '')
    assets = {key: row for key, row in assets.items()
              if not row.get('product') or not product or row['product'] == product}
    requested = list(brief.get('asset_ids') or assets)
    caps = {key: _capability(row) for key, row in assets.items()}
    required_view = requirements.get('required_view') or ''
    primary_region_id = str(requirements.get('primary_region_id') or '')
    eligible = [key for key in requested if key in caps and caps[key]['product_hero']
                and (not required_view or required_view in caps[key]['verified_visible_views'])]
    alternatives = [key for key in caps if key not in eligible and caps[key]['product_hero']
                    and (not required_view or required_view in caps[key]['verified_visible_views'])]
    reselected = not eligible and bool(alternatives)
    if reselected:
        eligible = alternatives
    photos = [key for key in requested if key in caps and caps[key]['scene_photo']]
    method = 'native_assembly' if eligible else 'source_photo' if photos else 'editorial_information'
    platform_name = platform.get('platform', '')
    task = requirements.get('reading_task') or platform.get('surface') or 'explain_one_message'
    sequence = list(platform.get('image_sequence') or ['cover'])
    if platform_name in {'朋友圈', 'moments'}:
        sequence = ['one_observation_or_invitation']
    elif task == 'article_body':
        sequence = ['one_explanation_with_evidence']
    elif task == 'poster':
        sequence = ['single_message_and_action']
    selection = {
        'recommended_method': method, 'hero_asset_ids': eligible,
        'requested_asset_ids': requested, 'reselected_from_available': reselected,
        'scene_asset_ids': photos, 'required_view': required_view,
        'primary_region_id': primary_region_id,
        'required_view_scope': 'primary_region' if primary_region_id else 'product_regions',
        'missing_view': bool(required_view and not eligible),
        'on_missing_material': 'Reselect authorized exact-product material or switch to a source-photo/information-led treatment; do not enlarge a page screenshot into a product hero.',
        'assets': {key: {**caps[key], 'source_id': row.get('source_id', ''),
                         'sha256': row.get('sha256', ''), 'sku_scope': row.get('sku_scope', '')}
                   for key, row in assets.items()},
    }
    contract = {
        'version': 'craft.v1', 'communication': {'single_proposition': proposition,
            'origin': proposition_origin, 'needs_art_direction': not bool(proposition),
            'reading_task': task, 'audience': brief.get('audience', ''),
            'publisher_kind': brief.get('publisher_kind', 'store'),
            'visual_bridge': requirements.get('visual_bridge', ''),
            'bridge_instruction': 'Make one visible relation between the message and the product; a clean product KV is valid. Do not invent customer events or product operations to add life.'},
        'roles': {'product': 'Preserve exact structure, color and visible source angle; carry recognition or demonstrated function.',
                  'type': 'Carry the single proposition and necessary explanation; do not repeat the complete article.',
                  'environment': 'Support the same proposition through scale, color or context; concept scenery is not product sample evidence.'},
        'reading': {'platform': platform_name, 'surface': platform.get('surface', ''),
                    'sequence': sequence, 'title_cover_relation': platform.get('title_cover_relation', ''),
                    'hierarchy': ['first_glance_single_proposition', 'product_or_explanation', 'optional_local_action'],
                    'safe_margin': platform.get('safe_margin', .065)},
        'source_selection': selection,
        'render_controls': {'schema': 'LayoutRegion', 'region_fields': list(REGION_CONTROLS),
                            'palette_fields': ['background', 'foreground', 'accent'],
                            'scope': '2D placement, typography, stacking and display shadows; no physical relighting or 3D viewpoint synthesis'},
        'model_intentions': {
            'light_material': 'Specify one coherent light direction, metal/glass response and contact relation; source product pixels remain source pixels. A floating concept must read as intentional, not an accidental missing shadow.',
            'contact': 'Decide supported, held or intentionally suspended; do not invent unsafe physical actions or hidden product geometry.',
            'typography': 'Choose font role, weight, letter spacing, line height and meaningful phrase breaks for this message. Preserve approved wording and conditions; do not draw faux glyphs.',
            'verification': 'Inspect actual output for edge halos, contact mismatch, material inconsistency, glyph shapes and reading order; structural checks cannot certify these.'},
        'locks': {'product': product, 'texts': texts,
                  'source_assets': {key: {'sha256': row.get('sha256', ''), 'source_id': row.get('source_id', ''),
                                          'sku_scope': row.get('sku_scope', '')} for key, row in assets.items()},
                  'fact_review_boundary': 'Existing independent fact review remains authoritative; this contract grants no new facts or asset permissions.'},
    }
    contract['id'] = 'craft-' + _digest(contract)[:20]
    return contract


def _selected(plan):
    plan = _data(plan)
    concepts = plan.get('concepts') or []
    return next((_data(row) for row in concepts if _data(row).get('id') == plan.get('selected_concept_id')), {})


def _regions(plan):
    selected = _selected(plan)
    pages = selected.get('pages') or [{'id': 'cover', 'composition': selected.get('composition', [])}]
    return [(str(_data(page).get('id') or 'cover'), _data(region))
            for page in pages for region in _data(page).get('composition', [])]


def check_craft_contract(contract, actual_plan, document=None):
    """Locate objective craft mismatches; no aesthetic or human-preference score."""
    issues = []
    def add(code, page, region, target, detail):
        issues.append({'code': code, 'page_id': page, 'region_id': region,
                       'target': target, 'detail': detail})
    assets = contract['source_selection']['assets']
    texts = contract['locks']['texts']
    plan = _data(actual_plan)
    plan_texts = _data(plan.get('brief')).get('texts')
    if plan_texts is not None and plan_texts != texts:
        add('approved_copy_changed', '', '', 'brief.texts', 'Retain the approved text bank during craft refinement; new copy requires its existing approval path.')
    canvas = _data(_data(plan.get('brief')).get('platform'))
    cw, ch = canvas.get('width', 1080), canvas.get('height', 1440)
    seen = set()
    for page, region in _regions(plan):
        rid = region.get('id') or ''
        if not rid:
            add('unaddressable_region', page, rid, 'id', 'Assign a stable region ID for a local revision.')
        elif (page, rid) in seen:
            add('duplicate_region_id', page, rid, 'id', 'A local revision must identify exactly one region.')
        seen.add((page, rid))
        if region.get('role') == 'image':
            aid = region.get('asset_id', '')
            asset = assets.get(aid)
            if asset is None:
                add('unknown_source_asset', page, rid, 'asset_id', 'Reselect from the supplied source-bound assets.')
                continue
            hero = region.get('image_kind', 'product') == 'product'
            if hero and not asset['product_hero']:
                add('unsuitable_product_hero', page, rid, 'asset_id', 'Use an inspected product subject or change the treatment; a screenshot/detail panel is not a product cutout.')
            required = contract['source_selection']['required_view']
            primary = contract['source_selection'].get('primary_region_id')
            if hero and required and (not primary or rid == primary) and required not in asset['verified_visible_views']:
                add('missing_source_view', page, rid, 'asset_id', 'The requested view is absent from inspected source metadata; reselect material or revise the viewpoint intent.')
            w, h = asset['width'], asset['height']
            if w and h:
                # Product uses contain: measure its actual fitted display, not the
                # full box. Photo slice uses cover. Neither predicts sharpness.
                scale = (min if hero else max)(region.get('width', 0)*cw/w, region.get('height', 0)*ch/h)
                if scale > 2:
                    add('source_upscale_over_2x', page, rid, 'asset_id', f'Computed display scale {scale:.2f}x; choose a larger real source or a smaller/different treatment. This is a resolution check, not a taste score.')
        if region.get('role') not in {'image', 'shape', 'rule'}:
            tid = region.get('text_id')
            if tid not in texts:
                add('unknown_approved_text', page, rid, 'text_id', 'Select an approved text reference without rewriting product claims.')
        for field, low, high in [('letter_spacing', -6, 30), ('line_height', .95, 1.8)]:
            value = region.get(field)
            if value is not None and (not isinstance(value, (int, float)) or not math.isfinite(value) or not low <= value <= high):
                add('unsupported_type_control', page, rid, field, 'Use a finite value inside the compiler control range.')
    if document is not None:
        doc = _data(document)
        for aid, actual in (doc.get('assets') or {}).items():
            locked = contract['locks']['source_assets'].get(aid)
            if locked and any(_data(actual).get(key, '') != locked[key] for key in ('sha256', 'source_id')):
                add('source_binding_changed', '', '', f'assets.{aid}', 'Restore the authorized bytes/source binding; changing a visual layout does not authorize replacing the product.')
    primary = contract['source_selection'].get('primary_region_id')
    if primary and not any(region.get('id') == primary for _,region in _regions(plan)):
        add('missing_primary_region','','','id','The commissioned primary product region is absent.')
    return {'contract_id': contract['id'], 'structural_issues': issues,
            'requires_actual_pixel_review': True, 'fact_audit_replaced': False,
            'score': None}


def build_craft_revision(contract, actual_plan, *, page_id, region_id, goal, controls):
    """Address one region, retaining all copy/source and other layout regions.

    The returned payload is a work order, not an applied mutation. Callers must
    keep their normal plan validation, source proof and actual-pixel acceptance.
    """
    if not str(goal).strip():
        raise ValueError('craft_revision_goal_required')
    controls = list(dict.fromkeys(controls))
    if not controls or not set(controls) <= set(REGION_CONTROLS):
        raise ValueError('craft_revision_unsupported_control')
    regions = _regions(actual_plan)
    targets = [row for page, row in regions if page == page_id and row.get('id') == region_id]
    if len(targets) != 1:
        raise ValueError('craft_revision_target_not_unique')
    target = targets[0]
    if set(controls) & _TYPE and target.get('role') in {'image', 'shape', 'rule'}:
        raise ValueError('craft_revision_type_requires_text')
    if 'contact_shadow' in controls and target.get('role') != 'image':
        raise ValueError('craft_revision_contact_shadow_requires_image')
    frozen = deepcopy(_data(actual_plan))
    return {'contract_id': contract['id'], 'page_id': page_id, 'region_id': region_id,
            'goal': str(goal), 'allowed_region_controls': controls,
            'locked_region_fields': {key: deepcopy(value) for key, value in target.items() if key not in controls},
            'locked_other_regions': [{'page_id': page, 'region': deepcopy(row)} for page, row in regions
                                     if not (page == page_id and row.get('id') == region_id)],
            'locked_sources_and_copy': deepcopy(contract['locks']),
            'base_plan_digest': _digest(frozen), 'base_plan': frozen,
            'requires_actual_pixel_review': True,
            'light_control_boundary': 'Display shadow or an explicit ground-contact ellipse only; source relighting, material changes and novel viewpoints are not native controls.' if set(controls) & _LIGHT else ''}


def check_craft_revision(order, revised_plan):
    """Prove that a proposed plan changed only the commissioned region controls.

    This does not certify the control values or their rendered appearance. The
    existing DesignPlan validator and image acceptance still run afterwards.
    """
    baseline = deepcopy(order['base_plan'])
    if _digest(baseline) != order['base_plan_digest']:
        raise ValueError('craft_revision_base_digest_changed')
    candidate = deepcopy(_data(revised_plan))
    before = [row for page, row in _regions(baseline)
              if page == order['page_id'] and row.get('id') == order['region_id']]
    after = [row for page, row in _regions(candidate)
             if page == order['page_id'] and row.get('id') == order['region_id']]
    if len(before) != 1 or len(after) != 1:
        return {'within_scope': False, 'issues': ['craft_revision_target_not_unique']}
    # Replace only permitted controls in a copy of the baseline, then compare
    # the full plan. This catches edits to other concepts, copy or source refs.
    selected = next(row for row in baseline['concepts'] if row['id'] == baseline['selected_concept_id'])
    pages = selected.get('pages') or [{'id': 'cover', 'composition': selected.get('composition', [])}]
    target = next(region for page in pages if page['id'] == order['page_id']
                  for region in page.get('composition', []) if region.get('id') == order['region_id'])
    for key in order['allowed_region_controls']:
        if key in after[0]:
            target[key] = deepcopy(after[0][key])
        else:
            target.pop(key, None)
    okay = baseline == candidate
    return {'within_scope': okay, 'issues': [] if okay else ['craft_revision_changed_locked_content'],
            'requires_actual_pixel_review': True}
