"""One escaped renderer and verified byte snapshot for preview and publication handoff."""
from __future__ import annotations

import base64
import hashlib
import html
import io
import json
import zipfile
from dataclasses import dataclass
from typing import Awaitable, Callable

from fastapi import HTTPException
from PIL import Image

RENDERER_VERSION = 'publication-1'
MAX_IMAGE = 12 * 1024 * 1024
MAX_TOTAL = 24 * 1024 * 1024


def digest(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def plain_text(record: dict, platform: str) -> str:
    parts = [record.get('title'), record.get('intro')]
    for block in record.get('blocks') or []:
        parts.append('\n'.join(str(x) for x in [block.get('text'), *(block.get('disclosures') or [])] if x))
    parts.append(record.get('cta'))
    if platform != 'moments' and record.get('tags'):
        parts.append(' '.join('#' + str(tag).lstrip('#') for tag in record['tags']))
    return '\n\n'.join(str(x) for x in parts if x)


@dataclass(frozen=True)
class Picture:
    asset_id: str
    filename: str
    mime: str
    sha256: str
    data: bytes
    caption: str
    block_index: int | None


@dataclass(frozen=True)
class PublicationBundle:
    run_id: str
    source_version: int
    platform: str
    content_hash: str
    text: str
    rich_html: str
    pictures: tuple[Picture, ...]

    def document(self) -> str:
        return '<!doctype html><html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"></head><body style="margin:0;background:#fff">' + self.rich_html + '</body></html>'

    def manifest(self) -> dict:
        return {'renderer_version': RENDERER_VERSION, 'run_id': self.run_id,
                'source_version': self.source_version, 'platform': self.platform,
                'content_hash': self.content_hash, 'plain_text': self.text,
                'rich_html': self.rich_html,
                'images': [{'filename': p.filename, 'sha256': p.sha256,
                            'bytes': len(p.data), 'mime': p.mime} for p in self.pictures]}

    def archive(self) -> bytes:
        files = {'article.html': self.document().encode('utf-8'), 'text.txt': self.text.encode('utf-8')}
        files.update({p.filename: p.data for p in self.pictures})
        manifest = {k: v for k, v in self.manifest().items() if k not in {'plain_text', 'rich_html'}}
        manifest['files'] = [{'path': name, 'sha256': digest(data), 'bytes': len(data)} for name, data in files.items()]
        files['manifest.json'] = json.dumps(manifest, ensure_ascii=False, indent=2).encode('utf-8')
        stream = io.BytesIO()
        with zipfile.ZipFile(stream, 'w', zipfile.ZIP_DEFLATED) as archive:
            for name, data in files.items():
                archive.writestr(name, data)
        return stream.getvalue()


async def build_bundle(record: dict, run_id: str, version: int, platform: str,
                       loader: Callable[[str], Awaitable[tuple[bytes, str]]]) -> PublicationBundle:
    if platform not in {'moments', 'wechat'}:
        raise HTTPException(422, '这篇内容的平台暂不支持图文打包')
    if record.get('status') != 'ready' or any(item.get('level') == 'error' or item.get('severity') == 'error'
                                            for item in record.get('audit') or []):
        raise HTTPException(409, '请先完成这篇内容，再下载图文')
    declared = {a.get('id'): a for a in record.get('assets') or [] if isinstance(a, dict)}
    placements = record.get('images') or []
    pictures = []
    seen = set()
    total = 0
    for placement in placements:
        aid = placement.get('asset_id')
        index = placement.get('block_index')
        if index is not None and (not isinstance(index, int) or index < 0 or index >= len(record.get('blocks') or [])):
            raise HTTPException(409, '配图位置已变化，请重新打开这篇内容')
        if aid in seen:
            continue
        if not aid or aid not in declared:
            raise HTTPException(409, '配图信息已变化，请重新打开这篇内容')
        seen.add(aid)
        raw, mime = await loader(aid)
        if not raw or len(raw) > MAX_IMAGE or total + len(raw) > MAX_TOTAL:
            raise HTTPException(422, '配图文件过大或已不可用')
        try:
            with Image.open(io.BytesIO(raw)) as image:
                actual_mime = Image.MIME.get(image.format)
                image.verify()
        except Exception:
            raise HTTPException(409, '有一张配图无法打开，请重新选择') from None
        extensions = {'image/png': 'png', 'image/jpeg': 'jpg', 'image/webp': 'webp'}
        if mime not in extensions or mime != actual_mime:
            raise HTTPException(409, '配图格式已变化，请重新选择')
        sha = digest(raw)
        if declared[aid].get('sha256') and declared[aid]['sha256'] != sha:
            raise HTTPException(409, '配图已更新，请重新打开这篇内容')
        total += len(raw)
        pictures.append(Picture(aid, f'images/{len(pictures)+1:02d}.{extensions[mime]}', mime, sha, raw,
                                str(placement.get('caption') or ''), index))
    escape = lambda value: html.escape(str(value or ''), quote=True)
    def paragraph(value):
        return '<p style="margin:0 0 22px;white-space:pre-wrap;overflow-wrap:anywhere">' + escape(value) + '</p>' if value else ''
    def figure(p, caption):
        uri = 'data:' + p.mime + ';base64,' + base64.b64encode(p.data).decode('ascii')
        return '<figure style="margin:24px 0"><img style="display:block;width:100%;height:auto" src="' + uri + '" alt="' + escape(caption) + '">' + ('<figcaption style="margin-top:8px;color:#626861;font-size:14px">' + escape(caption) + '</figcaption>' if caption else '') + '</figure>'
    by_id = {p.asset_id: p for p in pictures}
    def placed_images(index):
        # A file is packaged once but every deliberate placement stays in the article.
        return ''.join(figure(by_id[item['asset_id']], item.get('caption') or '')
                       for item in placements if item.get('block_index') == index)
    body = '<h1 style="font-size:27px;line-height:1.45;margin:0 0 26px">' + escape(record.get('title')) + '</h1>'
    body += paragraph(record.get('intro'))
    for index, block in enumerate(record.get('blocks') or []):
        body += paragraph(block.get('text'))
        body += ''.join(paragraph(note) for note in block.get('disclosures') or [])
        body += placed_images(index)
    body += placed_images(None)
    body += paragraph(record.get('cta'))
    if platform != 'moments':
        body += paragraph(' '.join('#' + str(tag).lstrip('#') for tag in record.get('tags') or []))
    rich_html = '<section style="max-width:640px;margin:auto;padding:28px 22px;color:#252d27;font-family:system-ui,\'PingFang SC\',\'Microsoft YaHei\',sans-serif;font-size:17px;line-height:1.9;letter-spacing:.3px">' + body + '</section>'
    text = plain_text(record, platform)
    identity = json.dumps({'renderer': RENDERER_VERSION, 'run_id': run_id, 'version': version,
                           'platform': platform, 'status': record['status'], 'html': rich_html,
                           'text': text, 'images': [(p.asset_id, p.sha256, p.block_index) for p in pictures]},
                          ensure_ascii=False, sort_keys=True).encode('utf-8')
    return PublicationBundle(run_id, version, platform, digest(identity), text, rich_html, tuple(pictures))
