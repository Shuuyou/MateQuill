import asyncio
import hashlib
import io
import json
import zipfile
from copy import deepcopy

import pytest
from fastapi import HTTPException
from PIL import Image

from app.publication_bundle import build_bundle


def picture(color='red'):
    buffer = io.BytesIO()
    Image.new('RGB', (12, 9), color).save(buffer, 'PNG')
    return buffer.getvalue()


def record():
    return {'status': 'ready', 'title': '把这一刻留下', 'intro': '正文开篇',
            'blocks': [{'text': '场景正文', 'disclosures': ['适用设备限定']}],
            'cta': '来店里试试看', 'tags': ['校园'], 'audit': [],
            'assets': [{'id': 'one'}, {'id': 'two'}],
            'images': [{'asset_id': 'two', 'block_index': 0, 'caption': '第二张先看'},
                       {'asset_id': 'one', 'block_index': None, 'caption': '第一张后看'}]}


def build(value=None, **kwargs):
    async def loader(aid):
        return picture('red' if aid == 'one' else 'blue'), 'image/png'
    return asyncio.run(build_bundle(record() if value is None else value, 'run-a', kwargs.pop('version', 3),
                                    kwargs.pop('platform', 'wechat'), kwargs.pop('loader', loader)))


def test_preview_copy_offline_are_one_renderer_and_zip_exact_file_hashes():
    bundle = build()
    assert bundle.rich_html in bundle.document()
    assert bundle.manifest()['rich_html'] == bundle.rich_html
    with zipfile.ZipFile(io.BytesIO(bundle.archive())) as archive:
        assert archive.read('article.html').decode() == bundle.document()
        assert archive.read('text.txt').decode() == bundle.text
        manifest = json.loads(archive.read('manifest.json'))
        for item in manifest['files']:
            data = archive.read(item['path'])
            assert hashlib.sha256(data).hexdigest() == item['sha256']
            assert len(data) == item['bytes']
        assert archive.read('images/01.png') == picture('blue')
        assert archive.read('images/02.png') == picture('red')
    assert '适用设备限定' in bundle.text and '适用设备限定' in bundle.rich_html


@pytest.mark.parametrize('field', ['title', 'intro', 'cta'])
def test_public_fields_escape_html_and_no_internal_strategy(field):
    value = record(); value[field] = '<script>alert(1)</script><img src=x onerror=1>'
    value['strategy'] = {'private_memory': 'secret-do-not-publish'}
    bundle = build(value)
    assert '<script>' not in bundle.rich_html and '&lt;script&gt;' in bundle.rich_html
    assert 'secret-do-not-publish' not in bundle.document()
    assert 'https:' not in bundle.document()


@pytest.mark.parametrize('status', ['draft', 'blocked', 'error', 'needs_input'])
def test_only_completed_ready_delivery(status):
    value = record(); value['status'] = status
    with pytest.raises(HTTPException) as error: build(value)
    assert error.value.status_code == 409


def test_ready_does_not_override_fact_error():
    value = record(); value['audit'] = [{'severity': 'error', 'category': 'fact'}]
    with pytest.raises(HTTPException): build(value)


def test_hash_binds_version_text_order_and_actual_bytes():
    original = build()
    assert original.content_hash != build(version=4).content_hash
    changed = record(); changed['intro'] += '新'
    assert original.content_hash != build(changed).content_hash
    changed = record(); changed['images'].reverse()
    assert original.content_hash != build(changed).content_hash
    async def changed_file(aid): return picture('green'), 'image/png'
    assert original.content_hash != build(loader=changed_file).content_hash


@pytest.mark.parametrize('failure', ['missing', 'broken', 'wrong_mime', 'hash', 'undeclared', 'position'])
def test_no_successful_package_can_silently_drop_bad_picture(failure):
    value = record()
    async def loader(aid):
        if failure == 'missing': raise HTTPException(404, 'private or missing')
        if failure == 'broken': return b'not-a-picture', 'image/png'
        return picture(), 'image/jpeg' if failure == 'wrong_mime' else 'image/png'
    if failure == 'hash': value['assets'][0]['sha256'] = '0' * 64
    if failure == 'undeclared': value['assets'] = []
    if failure == 'position': value['images'][0]['block_index'] = 99
    with pytest.raises(HTTPException): build(value, loader=loader)


def test_no_picture_is_a_valid_text_deliverable_and_moments_no_tags():
    value = record(); value['images'] = []; value['assets'] = []
    bundle = build(value, platform='moments')
    assert not bundle.pictures and '#校园' not in bundle.text
    assert bundle.archive()


def test_duplicate_placement_does_not_duplicate_download_file():
    value = record(); value['images'].append(deepcopy(value['images'][0]))
    bundle = build(value)
    assert len(bundle.pictures) == 2
    assert bundle.rich_html.count('<img ') == 3
