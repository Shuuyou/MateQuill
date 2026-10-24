"""Collect exact-product, hash-reviewed transparent subjects from the catalogue.

No caller URL, path, title-only classification, segmentation or network fallback.
The catalogue proves source binding; the pixel registry describes visible content.
"""
from __future__ import annotations

import hashlib
import io
import base64
from pathlib import Path

from PIL import Image

from ..local_knowledge import LocalKnowledgeProvider
from ..models import EvidenceBundle
from ..visual_assets import review_asset_bytes
from ..workflow import norm
from .asset_preparation import PreparationUnsupported, prepare_product_asset

PACKSHOT_ROLES = {'product_packshot', 'packshot'}


def _prepared_item(raw, metadata, review):
    digest = hashlib.sha256(raw).hexdigest()
    prepared = prepare_product_asset(raw, asset_kind='packshot', expected_sha256=digest)
    item = prepared.as_asset({key: value for key, value in metadata.items()
                              if key not in {'url', 'local_path'}})
    with Image.open(io.BytesIO(prepared.derived_bytes)) as image:
        histogram = image.getchannel('A').histogram()
        opacity = sum(alpha * count for alpha, count in enumerate(histogram)) / (255 * image.width * image.height)
    item.update(asset_kind='packshot', prepared_subject=True,
                catalog_sha256=digest, source_sha256=digest, visual_role='product_packshot',
                alpha_fraction=1-opacity,
                alpha_fraction_definition='mean_transparency_of_prepared_canvas_including_partial_alpha',
                reference_purpose='immutable_product_pixels_for_composition')
    item['visible_views'] = [view for view in ('front', 'rear', 'side', 'detail')
                             if view in review.get('semantic_tags', [])]
    item['visible_views_basis'] = 'pixel_review_sha256'
    return item


def _rank_subjects(rows, required_views, max_subjects=3):
    """Prefer requested observed views and usable pixels, with view diversity."""
    ordered = sorted(rows, key=lambda r: (
        -len(set(r[1].get('visible_views', [])) & set(required_views)),
        -(r[1].get('effective_width', 0) * r[1].get('effective_height', 0)), r[0]))
    result, covered = [], set()
    while ordered and len(result) < max_subjects:
        index = next((i for i, row in enumerate(ordered)
                      if set(row[1].get('visible_views', [])) - covered), 0)
        row = ordered.pop(index)
        result.append(row)
        covered.update(row[1].get('visible_views', []))
    return result


async def collect_product_subjects(product, existing_assets, *, required_views=(), max_subjects=3):
    """Return a bounded selection of prepared original-alpha subjects plus public source metadata.

    An already-authorized *unprepared* copy is replaced under the same asset ID;
    a prepared copy is deduplicated by its original catalogue SHA. Thus selecting
    an official source early does not accidentally disable subject preparation.
    """
    if type(max_subjects) is not int or not 1 <= max_subjects <= 8:
        raise ValueError('invalid_subject_count')
    product = str(product)
    existing = existing_assets if isinstance(existing_assets, dict) else {}
    required_views = tuple(view for view in required_views if view in {'front', 'rear', 'side', 'detail'})
    prepared_digests = set()
    for item in existing.values():
        if not isinstance(item, dict) or not item.get('prepared_subject'):
            continue
        preparation = item.get('preparation') or {}
        prepared_digests.add(item.get('catalog_sha256') or preparation.get('source_sha256'))
    result, source_times, seen, total = {}, {}, set(), 0
    candidates = []
    for identifier, metadata in existing.items():
        if (not isinstance(metadata, dict) or metadata.get('prepared_subject')
                or norm(metadata.get('product', '')) != norm(product)
                or not metadata.get('verified')
                or metadata.get('provenance') not in {'huawei_official', 'vmall_storefront'}
                or metadata.get('visual_role') not in PACKSHOT_ROLES
                or metadata.get('semantic_basis') != 'pixel_review_sha256'
                or not metadata.get('source_id')):
            continue
        data = metadata.get('data_url')
        if not isinstance(data, str) or not data.startswith(('data:image/png;base64,', 'data:image/webp;base64,')) or len(data) > 11_000_000:
            continue
        try:
            raw = base64.b64decode(data.split(',', 1)[1], validate=True)
            digest = hashlib.sha256(raw).hexdigest()
            if len(raw) > 8_000_000 or digest != metadata.get('sha256') or digest in prepared_digests:
                continue
            review = review_asset_bytes(raw, metadata.get('original_title') or metadata.get('title', ''))
            if (review.get('sha256') != digest or review.get('semantic_basis') != 'pixel_review_sha256'
                    or not review.get('usable') or review.get('visual_role') not in PACKSHOT_ROLES):
                continue
            item = _prepared_item(raw, metadata, review)
            if required_views and not set(item['visible_views']) & set(required_views):
                continue
            candidates.append((str(identifier), item, len(raw)))
        except (OSError, ValueError, PreparationUnsupported):
            continue
    for identifier, item, size in _rank_subjects(candidates, required_views, max_subjects):
        digest = item['catalog_sha256']
        if digest in seen or total + size > 20_000_000:
            continue
        result[identifier] = item
        seen.add(digest)
        total += size
    if len(result) >= max_subjects:
        return result, []  # Existing sources are already owned by the caller.
    provider = LocalKnowledgeProvider()
    bundle = EvidenceBundle(product=product)
    provider._add_official_assets(bundle, product, '透明产品主体 前后视图 外观')
    for asset in bundle.assets:
        if len(result) >= max_subjects:
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
            item = _prepared_item(raw, asset.model_dump(exclude={'url', 'local_path'}), review)
            if required_views and not set(item['visible_views']) & set(required_views):
                continue
        except (OSError, ValueError, PreparationUnsupported):
            continue
        result[asset.id] = item
        source_times[asset.source_id] = asset.captured_at
        seen.add(asset.sha256)
        total += size
    sources = [{**source.model_dump(), 'captured_at': source_times[source.id] or source.captured_at}
               for source in bundle.sources if source.id in source_times]
    return result, sources
