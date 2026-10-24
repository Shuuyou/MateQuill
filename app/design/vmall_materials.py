"""Expand only the exact VMall page/SKU already evidenced by a design run."""
from __future__ import annotations

import base64
import hashlib
import io

from PIL import Image

from ..vmall_knowledge import match_vmall_product, parameter_sku, prepare_assets
from .material_renditions import resolve_verified_rendition


def _data(value):
    return value.model_dump() if hasattr(value, 'model_dump') else dict(value or {})


def _existing_bytes(item):
    data = item.get('data_url')
    if not isinstance(data, str) or not data.startswith('data:image/') or ';base64,' not in data or len(data) > 11_000_000:
        return None
    try:
        raw = base64.b64decode(data.split(',', 1)[1], validate=True)
        if len(raw) > 8_000_000 or hashlib.sha256(raw).hexdigest() != item.get('sha256'):
            return None
        return raw
    except (ValueError, TypeError):
        return None


async def collect_vmall_design_materials(source, product, existing_assets):
    """Return <=8 same-SKU gallery metadata rows plus their existing page source.

    No legacy same-name migration: both the run's source and a SKU-scoped fact
    must bind the current unique page snapshot before any preparation/download.
    The caller retains normal source permission, design and pixel acceptance.
    """
    source = _data(source)
    row = match_vmall_product(product)
    if not row:
        return {}, []
    sku = parameter_sku(row)
    if not sku:
        return {}, []
    source_rows = [_data(item) for item in source.get('sources', [])]
    bound_sources = [item for item in source_rows if item.get('provider') == 'vmall_storefront'
                     and item.get('url') == row['source_url'] and item.get('sha256') == row['source_sha256']]
    ids = {item.get('id') for item in bound_sources if item.get('id')}
    scope = 'vmall:'+row['spu_id']+':'+sku
    if not ids or not any(_data(fact).get('sku_scope') == scope and _data(fact).get('source_id') in ids
                          for fact in source.get('facts', [])):
        return {}, []
    galleries = [asset for asset in row.get('assets', []) if isinstance(asset, dict)
                 and str(asset.get('spu_id', '')) == row['spu_id']
                 and str(asset.get('sku_id') or '') == sku and asset.get('scope') == 'sku_gallery']
    if not galleries:
        return {}, []
    existing = existing_assets if isinstance(existing_assets, dict) else {}
    checked = {key: raw for key, item in existing.items() if isinstance(item, dict)
               and (raw := _existing_bytes(item)) is not None}
    total = sum(len(raw) for raw in checked.values())
    # This is a delivery budget, not catalog/archive coverage. Do not start new
    # downloads when the caller's current delivery already consumes the budget.
    if total >= 20_000_000:
        return {}, []
    expected_source = 'vmall-image-'+hashlib.sha256((row['source_url']+row['source_sha256']).encode()).hexdigest()[:24]
    output, remaining, associated = {}, [], {}
    for asset in galleries:
        if len(output)+len(remaining) >= 8:
            break
        resolved = resolve_verified_rendition(row, asset)
        sha = resolved.get('sha256')
        reused = next(((key, item) for key, item in existing.items()
                       if key in checked and item.get('sha256') == sha
                       and item.get('source_id') == expected_source
                       and item.get('provenance') == 'vmall_storefront' and item.get('verified')), None)
        if reused:
            key, item = reused
            output[key] = {**item, 'sku_scope': scope, 'source_url': row['source_url'],
                           'source_page_sha256': row['source_sha256'], 'image_url': resolved['image_url']}
        else:
            remaining.append(asset)
        associated[resolved.get('sha256')] = resolved
    sources = []
    if remaining:
        prepared, sources, paths = await prepare_assets({**row, 'assets': remaining}, str(product), max_assets=8)
        for value in prepared:
            item = _data(value)
            identifier = item.get('id')
            path = paths.get(identifier)
            if not identifier or path is None:
                continue
            try:
                if not 0 < path.stat().st_size <= 8_000_000:
                    continue
                raw = path.read_bytes()
                if total+len(raw) > 20_000_000 or hashlib.sha256(raw).hexdigest() != item.get('sha256'):
                    continue
                with Image.open(io.BytesIO(raw)) as image:
                    if image.width*image.height > 24_000_000 or getattr(image, 'n_frames', 1) != 1:
                        continue
                    width, height = image.size
                    mime = Image.MIME.get(image.format, '')
                    image.verify()
                if mime not in {'image/png', 'image/jpeg', 'image/webp'}:
                    continue
                original = associated.get(item['sha256']) or next((a for a in remaining if a.get('sha256') == item['sha256']), {})
                metadata = {key: value for key, value in item.items() if key not in {'url', 'local_path'}}
                metadata.update(data_url='data:'+mime+';base64,'+base64.b64encode(raw).decode(),
                                width=width, height=height, sku_scope=scope,
                                source_url=row['source_url'], source_page_sha256=row['source_sha256'])
                if original:
                    metadata['image_url'] = original['image_url']
                output[identifier] = metadata
                total += len(raw)
            except (OSError, ValueError, Image.DecompressionBombError):
                continue
    used = {item.get('source_id') for item in output.values()}
    merged = {item['id']: item for value in [*bound_sources, *sources]
              if (item := _data(value)).get('id') in used}
    return output, list(merged.values())
