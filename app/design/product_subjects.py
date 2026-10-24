"""Collect exact-product, hash-reviewed transparent subjects from the catalogue.

No caller URL, path, title-only classification, segmentation or network fallback.
The catalogue proves source binding; the pixel registry describes visible content.
"""
from __future__ import annotations

import hashlib
import io
from pathlib import Path

from PIL import Image

from ..local_knowledge import LocalKnowledgeProvider
from ..models import EvidenceBundle
from ..visual_assets import review_asset_bytes
from ..workflow import norm
from .asset_preparation import PreparationUnsupported, prepare_product_asset

PACKSHOT_ROLES = {'product_packshot', 'packshot'}


async def collect_product_subjects(product, existing_assets):
    """Return <=3 prepared original-alpha subjects plus public source metadata.

    An already-authorized *unprepared* copy is replaced under the same asset ID;
    a prepared copy is deduplicated by its original catalogue SHA. Thus selecting
    an official source early does not accidentally disable subject preparation.
    """
    product = str(product)
    provider = LocalKnowledgeProvider()
    bundle = EvidenceBundle(product=product)
    provider._add_official_assets(bundle, product, '透明产品主体 前后视图 外观')
    existing = existing_assets if isinstance(existing_assets, dict) else {}
    prepared_digests = set()
    for item in existing.values():
        if not isinstance(item, dict) or not item.get('prepared_subject'):
            continue
        preparation = item.get('preparation') or {}
        prepared_digests.add(item.get('catalog_sha256') or preparation.get('source_sha256'))
    result, source_times, seen, total = {}, {}, set(), 0
    for asset in bundle.assets:
        if len(result) >= 3:
            break
        if (norm(asset.product) != norm(product) or not asset.verified
                or asset.provenance != 'huawei_official'
                or asset.visual_role not in PACKSHOT_ROLES
                or asset.semantic_basis != 'pixel_review_sha256'
                or not asset.sha256 or asset.sha256 in seen or asset.sha256 in prepared_digests):
            continue
        path = await provider.get_asset(asset.id)
        if not path:
            continue
        try:
            path = Path(path)
            size = path.stat().st_size
            if size > 8_000_000 or size + total > 20_000_000:
                continue
            raw = path.read_bytes()
            if hashlib.sha256(raw).hexdigest() != asset.sha256:
                continue
            review = review_asset_bytes(raw, asset.original_title or asset.title)
            if (not review.get('usable') or review.get('semantic_basis') != 'pixel_review_sha256'
                    or review.get('visual_role') not in PACKSHOT_ROLES):
                continue
            prepared = prepare_product_asset(raw, asset_kind='packshot', expected_sha256=asset.sha256)
        except (OSError, ValueError, PreparationUnsupported):
            continue
        item = prepared.as_asset(asset.model_dump(exclude={'url', 'local_path'}))
        with Image.open(io.BytesIO(prepared.derived_bytes)) as image:
            histogram = image.getchannel('A').histogram()
            opacity = sum(alpha * count for alpha, count in enumerate(histogram)) / (255 * image.width * image.height)
        item.update(asset_kind='packshot', prepared_subject=True,
                    catalog_sha256=asset.sha256, source_sha256=asset.sha256,
                    visual_role='product_packshot',
                    alpha_fraction=1-opacity,
                    alpha_fraction_definition='mean_transparency_of_prepared_canvas_including_partial_alpha',
                    reference_purpose='immutable_product_pixels_for_composition')
        item['visible_views'] = [view for view in ('front', 'rear', 'side', 'detail')
                                 if view in review.get('semantic_tags', [])]
        item['visible_views_basis'] = 'pixel_review_sha256'
        result[asset.id] = item
        source_times[asset.source_id] = asset.captured_at
        seen.add(asset.sha256)
        total += size
    sources = [{**source.model_dump(), 'captured_at': source_times[source.id] or source.captured_at}
               for source in bundle.sources if source.id in source_times]
    return result, sources
