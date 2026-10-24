"""Selected asset identity and byte-bound observations, never performance facts.

The caller supplies a server-authorized EvidenceBundle. Paths are read only from
its selected asset records; no user filenames, URLs, fetching, or path repair.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import re

from .visual_assets import review_asset_bytes

MAX_ASSET_BYTES = 12 * 1024 * 1024


def _plain(value):
    return value.model_dump(mode='json') if hasattr(value, 'model_dump') else value


def build_asset_claim_evidence(draft, bundle) -> list[dict]:
    """Return scoped evidence rows for assets actually selected in this draft.

    Each row has id, kind, asset_id, product, source_id, sha256,
    allowed_surfaces, text and claim_scope. Kinds are asset_identity (catalogue
    binding, not pixel SKU recognition) and asset_appearance (current bytes
    matching a local registered observation). Missing proof emits no row.
    """
    draft, bundle = _plain(draft), _plain(bundle)
    selected = {}
    for index, placement in enumerate(draft.get('images', [])):
        if isinstance(placement, dict) and isinstance(placement.get('asset_id'), str):
            selected.setdefault(placement['asset_id'], []).append(f'image:{index}.caption')
    creative = draft.get('creative_plan') or {}
    for card in creative.get('cards', []) if isinstance(creative, dict) else []:
        if not isinstance(card, dict) or not isinstance(card.get('id'), str):
            continue
        surfaces = [f'card:{card["id"]}.{field}' for field in ('headline', 'body', 'caption', 'overlay_text')
                    if isinstance(card.get(field), str) and card[field].strip()]
        for identifier in card.get('asset_ids', []):
            if isinstance(identifier, str):
                selected.setdefault(identifier, []).extend(surfaces)
    assets = {}
    duplicate_ids = set()
    for asset in bundle.get('assets', []):
        if not isinstance(asset, dict) or not isinstance(asset.get('id'), str):
            continue
        if asset['id'] in assets:
            duplicate_ids.add(asset['id'])
        assets[asset['id']] = asset
    source_ids = {row.get('id') for row in bundle.get('sources', []) if isinstance(row, dict)}
    result = []
    for identifier, surfaces in selected.items():
        asset = assets.get(identifier)
        if not asset or identifier in duplicate_ids or not surfaces:
            continue
        digest = asset.get('sha256', '')
        if (asset.get('verified') is not True or not isinstance(asset.get('product'), str)
                or not asset['product'].strip() or asset.get('source_id') not in source_ids
                or not isinstance(digest, str) or not re.fullmatch('[0-9a-f]{64}', digest)):
            continue
        binding = {'asset_id': identifier, 'product': asset['product'], 'source_id': asset['source_id'],
                   'sha256': digest, 'allowed_surfaces': list(dict.fromkeys(surfaces))}
        fingerprint = hashlib.sha256(json.dumps({key: binding[key] for key in
            ('asset_id', 'product', 'source_id', 'sha256')}, ensure_ascii=False, sort_keys=True).encode()).hexdigest()[:24]
        result.append({**binding, 'id': 'asset_identity_' + fingerprint,
            'kind': 'asset_identity', 'text': asset['product'],
            'claim_scope': '仅证明本次授权目录中该asset_id绑定的产品身份；不是像素型号认证，不证明外观细节、功能、性能、亲历或背景真实性。'})
        local_path = asset.get('local_path')
        if (not isinstance(local_path, str) or not local_path or '://' in local_path
                or local_path.startswith(('\\\\', '//'))):
            continue
        try:
            path = Path(local_path)
            if not path.is_file() or not 0 < path.stat().st_size <= MAX_ASSET_BYTES:
                continue
            with path.open('rb') as stream:
                raw = stream.read(MAX_ASSET_BYTES + 1)
            if len(raw) > MAX_ASSET_BYTES or hashlib.sha256(raw).hexdigest() != digest:
                continue
            observation = review_asset_bytes(raw, asset.get('original_title') or asset.get('title') or '')
        except (OSError, ValueError):
            continue
        observed = observation.get('observed_title')
        if (observation.get('semantic_basis') != 'pixel_review_sha256'
                or observation.get('sha256') != digest or observation.get('usable') is not True
                or not isinstance(observed, str) or not observed.strip()):
            continue
        result.append({**binding, 'id': 'asset_appearance_' + fingerprint,
            'kind': 'asset_appearance', 'text': observed,
            'claim_scope': '仅支持本SHA256已登记观察中的可见外观；不认证型号、授权、功能、性能、亲历、本机样张或门店/背景真实性。'})
    return result
