import asyncio
import base64
import hashlib
import io
from pathlib import Path

from PIL import Image
import pytest

from app.design import vmall_materials as module


def fixture(monkeypatch, tmp_path):
    raw_io = io.BytesIO();Image.new('RGBA', (160, 160), (2, 3, 4, 100)).save(raw_io, format='PNG')
    raw = raw_io.getvalue();sha = hashlib.sha256(raw).hexdigest()
    row = {'name': 'Device', 'spu_id': '123', 'parameter_sku_id': '456', 'variants': [{'sku_id': '456'}],
           'source_url': 'https://www.vmall.com/product/123.html', 'source_sha256': 'a'*64,
           'assets': [{'id': 'gallery', 'spu_id': '123', 'sku_id': '456', 'scope': 'sku_gallery', 'sha256': sha, 'image_url': 'https://res.vmallres.com/gallery.png'},
                      {'id': 'other-sku', 'spu_id': '123', 'sku_id': '457', 'scope': 'sku_gallery'},
                      {'id': 'detail', 'spu_id': '123', 'sku_id': None, 'scope': 'product_detail'}]}
    sid = 'vmall-image-'+hashlib.sha256((row['source_url']+row['source_sha256']).encode()).hexdigest()[:24]
    source = {'sources': [{'id': sid, 'provider': 'vmall_storefront', 'url': row['source_url'], 'sha256': row['source_sha256']}],
              'facts': [{'sku_scope': 'vmall:123:456', 'source_id': sid}]}
    item = {'id': 'vmall-123-'+sha, 'product': 'Device', 'sha256': sha, 'source_id': sid,
            'provenance': 'vmall_storefront', 'verified': True, 'visual_role': 'unknown', 'semantic_basis': 'title_only'}
    path = tmp_path/'actual.png';path.write_bytes(raw)
    calls = []
    async def prepare(r, product, **kwargs):
        calls.append((r, kwargs));return [item], source['sources'], {item['id']: path}
    monkeypatch.setattr(module, 'match_vmall_product', lambda p: row if p == 'Device' else None)
    monkeypatch.setattr(module, 'resolve_verified_rendition', lambda r, a: dict(a))
    monkeypatch.setattr(module, 'prepare_assets', prepare)
    return row, source, item, raw, calls


def test_only_authorized_same_sku_gallery_with_real_bytes(monkeypatch, tmp_path):
    row, source, item, raw, calls = fixture(monkeypatch, tmp_path)
    output, sources = asyncio.run(module.collect_vmall_design_materials(source, 'Device', {}))
    assert [a['id'] for a in calls[0][0]['assets']] == ['gallery']
    assert calls[0][1]['max_assets'] == 8
    result = output[item['id']]
    assert result['width'] == result['height'] == 160
    assert result['sku_scope'] == 'vmall:123:456' and result['source_url'] == row['source_url']
    assert base64.b64decode(result['data_url'].split(',')[1]) == raw
    assert result['visual_role'] == 'unknown' and result['semantic_basis'] == 'title_only'
    assert sources == source['sources']


@pytest.mark.parametrize('change', ['page_hash', 'scope', 'provider', 'source_id', 'old_url'])
def test_unbound_run_never_requests_new_material(monkeypatch, tmp_path, change):
    row, source, item, raw, calls = fixture(monkeypatch, tmp_path)
    if change == 'page_hash':source['sources'][0]['sha256'] = 'b'*64
    if change == 'scope':source['facts'][0]['sku_scope'] = 'vmall:123:457'
    if change == 'provider':source['sources'][0]['provider'] = 'huawei_official'
    if change == 'source_id':source['facts'][0]['source_id'] = 'different-source'
    if change == 'old_url':source['sources'][0]['url'] = 'https://consumer.huawei.com/old-device'
    assert asyncio.run(module.collect_vmall_design_materials(source, 'Device', {})) == ({}, [])
    assert not calls


def test_existing_real_bytes_reused_without_prepare_call(monkeypatch, tmp_path):
    row, source, item, raw, calls = fixture(monkeypatch, tmp_path)
    existing = {item['id']: {**item, 'data_url': 'data:image/png;base64,'+base64.b64encode(raw).decode()}}
    output, sources = asyncio.run(module.collect_vmall_design_materials(source, 'Device', existing))
    assert list(output) == [item['id']] and not calls
    assert output[item['id']]['source_page_sha256'] == row['source_sha256']
    assert sources == source['sources']


def test_missing_exact_product_no_same_name_family_join(monkeypatch, tmp_path):
    _, source, _, _, calls = fixture(monkeypatch, tmp_path)
    assert asyncio.run(module.collect_vmall_design_materials(source, 'Device Pro', {})) == ({}, [])
    assert not calls
