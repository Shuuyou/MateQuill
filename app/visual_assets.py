"""Pixel observations bound to exact bytes, separate from SKU/provenance checks.

The review registry contains observations only: it cannot certify product identity,
authorization, features or performance. Unreviewed bytes stay explicitly unreviewed.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

from .facts import asset_semantics


REVIEW_PATH = Path(__file__).with_name('asset_visual_reviews.json')


def review_asset_bytes(raw: bytes, original_title: str) -> dict:
    digest = hashlib.sha256(raw).hexdigest()
    fallback = {**asset_semantics(original_title), 'title': original_title, 'original_title': original_title,
                'observed_title': '', 'usable': True, 'sha256': digest,
                'visual_group': '',
                'reason': '尚无与图片字节匹配的视觉审阅记录',
                'visual_review_warning': '素材标题尚未通过像素审阅，不能当作画面内容依据'}
    try:
        registry = json.loads(REVIEW_PATH.read_text(encoding='utf-8-sig'))
        records = registry.get('reviews', registry) if isinstance(registry, dict) else {}
        record = records.get(digest) if isinstance(records, dict) else None
    except (OSError, ValueError, TypeError):
        return fallback
    if not isinstance(record, dict):
        return fallback
    title = record.get('observed_title')
    tags = record.get('semantic_tags')
    role = record.get('visual_role')
    usable = record.get('usable')
    if (not isinstance(title, str) or not title.strip() or not isinstance(tags, list)
            or any(not isinstance(tag, str) for tag in tags)
            or not isinstance(role, str) or not isinstance(usable, bool)):
        fallback['visual_review_warning'] = '图片审阅记录不完整，已保留为未视觉核验素材'
        return fallback
    return {'title': title.strip(), 'observed_title': title.strip(), 'original_title': original_title,
            'semantic_tags': tags, 'visual_role': role, 'usable': usable,
            'visual_group': record.get('visual_group', '') if isinstance(record.get('visual_group', ''), str) else '',
            'semantic_basis': 'pixel_review_sha256', 'sha256': digest,
            'reason': str(record.get('reason', '')), 'visual_review_warning': '',
            'claim_scope': 'AI 视觉观察仅适用于本 SHA256 图片可见画面；不认证型号、参数、功能效果或素材授权'}
