import asyncio
import hashlib
import io

from PIL import Image

from app import design_brand_materials as module
from app.models import Asset, Source
from scripts.collect_official_assets import ProductImages, prioritized_candidates


def test_real_product_hero_gets_reserved_slot_without_guessing_urls():
    parser = ProductImages('https://consumer.huawei.com/cn/phones/mate90/', 'mate90')
    parser.feed('<img src="/content/dam/mate90/images/color.png" alt="背面">'
                '<img src="/content/dam/mate90/images/hero/kv.jpg">'
                '<img src="/content/dam/mate90-pro/images/hero/kv.jpg">')
    ordered = prioritized_candidates(parser.items)
    assert len(ordered) == 2
    assert ordered[0]['image_url'].endswith('/mate90/images/hero/kv.jpg')
    assert ordered[0]['visual_role'] == 'campaign_key_visual'


def setup_catalog(monkeypatch, tmp_path, *, corrupt=False, reviewed=True):
    image = Image.new('RGB', (600, 300), 'green'); stream = io.BytesIO(); image.save(stream, format='JPEG')
    raw = stream.getvalue(); sha = hashlib.sha256(raw).hexdigest()
    path = tmp_path / 'hero.jpg'; path.write_bytes(raw if not corrupt else b'changed')
    row = Asset(id='official-test', product='HUAWEI Mate 90', source_id='source',
                url='/api/assets/official-test', sha256=sha, verified=True,
                provenance='huawei_official', visual_role='campaign_key_visual',
                semantic_basis='pixel_review_sha256' if reviewed else 'title_only')
    class Provider:
        def _add_official_assets(self, bundle, product, query):
            bundle.assets.append(row)
            bundle.sources.append(Source(id='source', title='官方产品页', url='https://consumer.huawei.com/cn/phones/mate90/'))
        async def get_asset(self, identifier):
            return path
    monkeypatch.setattr(module, 'LocalKnowledgeProvider', Provider)
    monkeypatch.setattr(module, 'review_asset_bytes', lambda *args: {
        'usable': True, 'semantic_basis': 'pixel_review_sha256', 'visual_role': 'campaign_key_visual'})
    return row


def test_reviewed_exact_product_visual_returns_bytes_and_source(monkeypatch, tmp_path):
    row = setup_catalog(monkeypatch, tmp_path)
    assets, sources = asyncio.run(module.collect_brand_materials('HUAWEI Mate 90', {}))
    assert assets[row.id]['data_url'].startswith('data:image/jpeg;base64,')
    assert 'local_path' not in assets[row.id] and 'url' not in assets[row.id]
    assert sources[0]['id'] == row.source_id
    duplicate = asyncio.run(module.collect_brand_materials('HUAWEI Mate 90', assets))
    assert duplicate == ({}, [])
    assert asyncio.run(module.collect_brand_materials('HUAWEI Mate 90 Pro', {})) == ({}, [])


def test_changed_bytes_never_become_design_reference(monkeypatch, tmp_path):
    setup_catalog(monkeypatch, tmp_path, corrupt=True)
    assert asyncio.run(module.collect_brand_materials('HUAWEI Mate 90', {})) == ({}, [])


def test_title_only_role_is_not_a_pixel_review(monkeypatch, tmp_path):
    setup_catalog(monkeypatch, tmp_path, reviewed=False)
    assert asyncio.run(module.collect_brand_materials('HUAWEI Mate 90', {})) == ({}, [])
