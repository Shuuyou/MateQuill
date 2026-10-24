"""Locally verified VMall HD renditions, bound to page/SKU/original URL.

No URL guessing, downloads, pixel classification or rights certification. Only
an already downloaded 1920 rendition of the exact 800 filename may be recorded.
"""
from __future__ import annotations

import hashlib
import io
import json
from pathlib import Path
import re
import threading
from urllib.parse import urlsplit

from PIL import Image

from ..config import ROOT
from ..vmall_catalog import safe_asset_url, utc_now

_LOCK = threading.Lock()
_SHA = re.compile(r'^[a-f0-9]{64}$')


def _key(row, asset):
    spu, sku = str(row.get('spu_id', '')), str(asset.get('sku_id') or '')
    page_sha = row.get('source_sha256', '')
    original = asset.get('image_url', '')
    if (not spu.isdigit() or not sku.isdigit() or str(asset.get('spu_id', '')) != spu
            or not isinstance(page_sha, str) or not _SHA.fullmatch(page_sha)
            or asset.get('scope') != 'sku_gallery' or not safe_asset_url(original)):
        raise ValueError('rendition_source_binding_invalid')
    return hashlib.sha256(json.dumps([spu, sku, original, page_sha], separators=(',', ':')).encode()).hexdigest()


def _same_rendition(original, highres):
    if not safe_asset_url(highres):
        return False
    a, b = urlsplit(original), urlsplit(highres)
    directory, _, filename = a.path.rpartition('/')
    match = re.fullmatch(r'800_800_(.+\.(?:png|jpg|jpeg|webp))', filename, re.I)
    return bool(match and a.netloc == b.netloc and b.path == directory+'/1920_1920_'+match[1]
                and a.query == b.query and not a.fragment and not b.fragment)


def _paths(root, registry_path):
    base = Path(root or ROOT).resolve()
    registry = Path(registry_path) if registry_path else base/'runtime/official-assets/vmall/verified-renditions.json'
    if not registry.is_absolute():
        registry = base/registry
    registry = registry.resolve()
    if not registry.is_relative_to(base/'runtime'):
        raise ValueError('rendition_registry_outside_runtime')
    return base, registry


def _read(registry):
    try:
        if registry.stat().st_size > 4_000_000:
            return {}
        value = json.loads(registry.read_text(encoding='utf-8-sig'))
        return value.get('items', {}) if isinstance(value, dict) and isinstance(value.get('items'), dict) else {}
    except (OSError, ValueError):
        return {}


def _verify_file(base, path, sha):
    file = Path(path)
    file = (file if file.is_absolute() else base/file).resolve()
    if not file.is_relative_to(base/'runtime') or not _SHA.fullmatch(str(sha)):
        raise ValueError('rendition_local_binding_invalid')
    if not 0 < file.stat().st_size <= 8_000_000:
        raise ValueError('rendition_byte_limit')
    raw = file.read_bytes()
    if hashlib.sha256(raw).hexdigest() != sha:
        raise ValueError('rendition_sha_mismatch')
    with Image.open(io.BytesIO(raw)) as image:
        if image.size != (1920, 1920) or getattr(image, 'n_frames', 1) != 1:
            raise ValueError('rendition_actual_dimensions_invalid')
        image.verify()
    return file, len(raw)


def register_verified_rendition(row, asset, *, image_url, path, expected_sha256,
                                registry_path=None, root=None):
    """Record an agent-inspected candidate only after local byte/dimension checks.

    Visual observations belong to the separate actual-SHA visual registry. This
    sidecar proves a tested rendition location, not whether its picture is a KV.
    """
    key = _key(row, asset)
    if not _same_rendition(asset['image_url'], image_url):
        raise ValueError('rendition_not_exact_800_to_1920')
    base, registry = _paths(root, registry_path)
    file, count = _verify_file(base, path, expected_sha256)
    record = {'spu_id': str(row['spu_id']), 'sku_id': str(asset['sku_id']),
              'original_url': asset['image_url'], 'source_page_sha256': row['source_sha256'],
              'original_sha256': asset.get('sha256', ''), 'image_url': image_url,
              'sha256': expected_sha256, 'width': 1920, 'height': 1920, 'bytes': count,
              'path': file.relative_to(base).as_posix(), 'verified_at': utc_now(),
              'verification': 'local_bytes_sha256_and_actual_dimensions',
              'visual_classification': 'separate_sha_bound_observation_required'}
    with _LOCK:
        items = _read(registry)
        items[key] = record
        registry.parent.mkdir(parents=True, exist_ok=True)
        temporary = registry.with_suffix('.tmp')
        temporary.write_text(json.dumps({'version': 'vmall-renditions.v1', 'items': items}, ensure_ascii=False), encoding='utf-8')
        temporary.replace(registry)
    return record


def resolve_verified_rendition(row, asset, *, registry_path=None, root=None):
    """Return a verified asset copy or unchanged original; never initiate I/O GET."""
    original = dict(asset)
    try:
        key = _key(row, asset)
        base, registry = _paths(root, registry_path)
        item = _read(registry).get(key)
        if not isinstance(item, dict) or not _same_rendition(asset['image_url'], item.get('image_url', '')):
            return original
        if (item.get('spu_id') != str(row['spu_id']) or item.get('sku_id') != str(asset['sku_id'])
                or item.get('source_page_sha256') != row['source_sha256']
                or item.get('original_url') != asset['image_url']
                or item.get('original_sha256', '') != asset.get('sha256', '')):
            return original
        file, count = _verify_file(base, item.get('path', ''), item.get('sha256', ''))
        return {**original, 'image_url': item['image_url'], 'sha256': item['sha256'],
                'width': 1920, 'height': 1920, 'bytes': count,
                'path': file.relative_to(base).as_posix(), 'download_status': 'downloaded',
                'rendition_source': {key: item[key] for key in ('original_url', 'original_sha256', 'source_page_sha256', 'spu_id', 'sku_id', 'verified_at')}}
    except (OSError, ValueError, TypeError, Image.DecompressionBombError):
        return original
