"""Stable references to separate publishable fields, never fuzzy quote repair.

The reviewer can identify an exact field without retyping Chinese copy. The
server fills only that field's actual text and preserves the judgement itself.
Candidate alternatives and storyboard text remain independent surfaces.
"""
from __future__ import annotations

from copy import deepcopy


def marketing_public_surfaces(draft) -> dict[str, str]:
    if hasattr(draft, 'model_dump'):
        draft = draft.model_dump(mode='json')
    if not isinstance(draft, dict):
        raise ValueError('Marketing draft must be an object')
    surfaces = {}

    def add(key, value):
        if isinstance(value, str):
            surfaces[key] = value

    def disclosures(prefix, value):
        if not isinstance(value, list):
            return
        if any(not isinstance(item, str) for item in value):
            raise ValueError('Public disclosures must be strings')
        surfaces[prefix + '.disclosures'] = '\n'.join(value)
        for index, text in enumerate(value):
            surfaces[f'{prefix}.disclosure:{index}'] = text

    for key in ('title', 'intro', 'cta'):
        add(key, draft.get(key, ''))
    for index, block in enumerate(draft.get('blocks') or []):
        if hasattr(block, 'model_dump'):
            block = block.model_dump(mode='json')
        if isinstance(block, dict):
            add(f'body:{index}', block.get('text', ''))
            disclosures(f'body:{index}', block.get('disclosures', []))
    # Some bounded callers provide public body/text directly rather than blocks.
    for key in ('body', 'text'):
        if isinstance(draft.get(key), str):
            add(key, draft[key])
    for index, image in enumerate(draft.get('images') or []):
        if hasattr(image, 'model_dump'):
            image = image.model_dump(mode='json')
        if isinstance(image, dict):
            add(f'image:{index}.caption', image.get('caption', ''))
    for index, tag in enumerate(draft.get('tags') or []):
        add(f'tag:{index}', tag)
    plan = draft.get('creative_plan') or {}
    if not isinstance(plan, dict):
        return surfaces
    seen = set()
    for card in plan.get('cards') or []:
        if not isinstance(card, dict) or not isinstance(card.get('id'), str) or not card['id']:
            continue
        identifier = card['id']
        if identifier in seen:
            raise ValueError('Duplicate public card identity')
        seen.add(identifier)
        prefix = f'card:{identifier}'
        for key in ('headline', 'body', 'caption', 'overlay_text'):
            add(prefix + '.' + key, card.get(key, ''))
        disclosures(prefix, card.get('disclosures', []))
    for index, variant in enumerate(plan.get('test_variants') or []):
        if isinstance(variant, dict):
            for key in ('title', 'cover_copy'):
                add(f'variant:{index}.{key}', variant.get(key, ''))
    return surfaces


def marketing_surface_role(surface_id: str) -> str:
    """Roles prevent selectable alternatives being treated as one repeated post."""
    if surface_id.startswith('variant:'):
        return 'candidate_alternative'
    if surface_id.startswith('card:'):
        return 'storyboard'
    if surface_id.startswith('image:'):
        return 'image_caption'
    return 'primary_post'


def resolve_surface_finding(finding: dict, public_surfaces: dict[str, str], *, allow_visual=False) -> dict:
    """Fill an empty quote from a known exact field ID; never change a judgement.

    Empty fields remain empty so a reviewer can point to a missing disclosure.
    The caller must recognize the known field as textual, not claim pixel access.
    """
    value = deepcopy(finding)
    quote, region = value.get('quote'), value.get('region')
    if not isinstance(region, str):
        raise ValueError('Marketing review finding is missing its region')
    if not isinstance(quote, str):
        # The schema allows an omitted quote (it defaults to ''); normalize
        # it here so every caller can safely index value['quote'] afterward.
        value['quote'] = quote = ''
    if region in public_surfaces:
        actual = public_surfaces[region]
        if not isinstance(actual, str):
            raise ValueError('Invalid public surface value')
        if quote and (not quote.strip() or quote not in actual):
            raise ValueError('Marketing review quotes non-public or nonexistent text')
        if not quote:
            value['quote'] = actual
        return value
    if region and not allow_visual:
        raise ValueError('Marketing review references unknown public surface')
    if quote and not any(quote.strip() and quote in text for text in public_surfaces.values()):
        raise ValueError('Marketing review quotes non-public or nonexistent text')
    if not quote and (not region or not allow_visual):
        raise ValueError('Marketing review references unknown public surface')
    return value
