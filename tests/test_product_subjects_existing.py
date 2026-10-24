import asyncio
import base64
import hashlib
import io
import json

from PIL import Image, ImageDraw

from app.design import product_subjects as module
from app import visual_assets


def setup(monkeypatch, tmp_path, rows):
    reviews = {}
    assets = {}
    for i, (view, pixels, role) in enumerate(rows):
        image = Image.new('RGBA', (pixels, pixels))
        ImageDraw.Draw(image).rectangle((10, 10, pixels-20, pixels-20), fill=(40+i, 80, 100, 255))
        buf = io.BytesIO();image.save(buf, format='PNG');raw = buf.getvalue()
        sha = hashlib.sha256(raw).hexdigest();aid = f'asset-{i}'
        reviews[sha] = {'observed_title': 'Actual visible subject', 'semantic_tags': [view],
                        'visual_role': role, 'usable': True}
        assets[aid] = {'product': 'Device', 'verified': True, 'provenance': 'vmall_storefront',
                       'source_id': 'source', 'sha256': sha, 'visual_role': 'product_packshot',
                       'semantic_basis': 'pixel_review_sha256', 'sku_scope': 'vmall:123:456',
                       'source_url': 'https://www.vmall.com/product/123.html',
                       'data_url': 'data:image/png;base64,'+base64.b64encode(raw).decode()}
    registry = tmp_path/'reviews.json';registry.write_text(json.dumps({'reviews': reviews}))
    monkeypatch.setattr(visual_assets, 'REVIEW_PATH', registry)
    class EmptyProvider:
        def _add_official_assets(self, *args):pass
    monkeypatch.setattr(module, 'LocalKnowledgeProvider', EmptyProvider)
    return assets


def test_existing_vmall_source_alpha_and_internal_sku_preserved(monkeypatch, tmp_path):
    assets = setup(monkeypatch, tmp_path, [('rear', 180, 'product_packshot')])
    output, sources = asyncio.run(module.collect_product_subjects('Device', assets))
    row = output['asset-0']
    assert row['preparation']['operation']['backend'] == 'source_alpha'
    assert row['sku_scope'] == 'vmall:123:456'
    assert row['source_url'] == assets['asset-0']['source_url']
    assert row['catalog_sha256'] == assets['asset-0']['sha256']
    assert row['visible_views'] == ['rear'] and sources == []


def test_registry_actual_sha_overrides_claimed_packshot_metadata(monkeypatch, tmp_path):
    assets = setup(monkeypatch, tmp_path, [('rear', 180, 'campaign_key_visual')])
    assert asyncio.run(module.collect_product_subjects('Device', assets)) == ({}, [])
    assets = setup(monkeypatch, tmp_path, [('rear', 180, 'product_packshot')])
    assets['asset-0']['sha256'] = 'f'*64
    assert asyncio.run(module.collect_product_subjects('Device', assets)) == ({}, [])


def test_view_specific_selection_and_diversity_instead_of_first_three(monkeypatch, tmp_path):
    assets = setup(monkeypatch, tmp_path, [('rear', 180, 'product_packshot'),
        ('rear', 190, 'product_packshot'), ('rear', 200, 'product_packshot'),
        ('front', 210, 'product_packshot'), ('side', 180, 'product_packshot')])
    output, _ = asyncio.run(module.collect_product_subjects('Device', assets))
    assert {x['visible_views'][0] for x in output.values()} == {'front', 'rear', 'side'}
    output, _ = asyncio.run(module.collect_product_subjects('Device', assets, required_views=['front']))
    assert list(output) == ['asset-3']
    assert asyncio.run(module.collect_product_subjects('Other Device', assets)) == ({}, [])


def test_unverified_and_unreviewed_alpha_are_not_subjects(monkeypatch, tmp_path):
    assets = setup(monkeypatch, tmp_path, [('front', 180, 'product_packshot')])
    assets['asset-0']['verified'] = False
    assert asyncio.run(module.collect_product_subjects('Device', assets)) == ({}, [])
    assets['asset-0']['verified'] = True
    visual_assets.REVIEW_PATH.write_text('{}')
    assert asyncio.run(module.collect_product_subjects('Device', assets)) == ({}, [])
