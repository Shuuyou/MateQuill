"""Hash-pinned launch visuals from the existing first-party asset catalogue.

No network discovery or caller URL/path is accepted. Product snapshots and
pixel reviews remain independent checks; the images inform design, not claims.
"""
from __future__ import annotations

import hashlib
from pathlib import Path

from .local_knowledge import LocalKnowledgeProvider
from .models import EvidenceBundle
from .poster import _raster_data
from .visual_assets import review_asset_bytes
from .workflow import norm


async def collect_brand_materials(product, existing_assets):
    """Return at most two reviewed same-product visuals and their sources.

    ``existing_assets`` are already authorized inputs, used only to deduplicate.
    The catalogue provider validates its root, source URL and exact file hash.
    """
    provider = LocalKnowledgeProvider()
    bundle = EvidenceBundle(product=str(product))
    provider._add_official_assets(bundle, str(product), '产品发布主视觉')
    existing = existing_assets if isinstance(existing_assets, dict) else {}
    digests = {row.get('sha256') for row in existing.values() if isinstance(row, dict)}
    result, source_times, total = {}, {}, 0
    for asset in bundle.assets:
        if len(result) >= 2:
            break
        if (asset.id in existing or asset.sha256 in digests or norm(asset.product) != norm(str(product))
                or not asset.verified or asset.visual_role != 'campaign_key_visual'
                or asset.semantic_basis != 'pixel_review_sha256'):
            continue
        path = await provider.get_asset(asset.id)
        if not path:
            continue
        path = Path(path)
        size = path.stat().st_size
        if size > 8_000_000 or total + size > 8_000_000:
            continue
        raw = path.read_bytes()
        if hashlib.sha256(raw).hexdigest() != asset.sha256:
            continue
        review = review_asset_bytes(raw, asset.original_title or asset.title)
        if (not review.get('usable') or review.get('semantic_basis') != 'pixel_review_sha256'
                or review.get('visual_role') != 'campaign_key_visual'):
            continue
        data = _raster_data(raw)
        if not data:
            continue
        item = asset.model_dump(exclude={'url', 'local_path'})
        item.update(data_url=data, reference_purpose='composition_and_color_reference')
        result[asset.id] = item
        source_times[asset.source_id] = asset.captured_at
        total += len(raw)
    return result, [{**source.model_dump(), 'captured_at': source_times[source.id] or source.captured_at}
                    for source in bundle.sources if source.id in source_times]
