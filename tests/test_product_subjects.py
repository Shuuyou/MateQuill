import asyncio
import hashlib
import io

from PIL import Image, ImageDraw

from app.design import product_subjects as module
from app.models import Asset, Source


PRODUCT = 'HUAWEI Mate 90'


def image_bytes(*, opaque=False, variant=0):
    image = Image.new('RGBA', (140, 140), (0, 0, 0, 255 if opaque else 0))
    ImageDraw.Draw(image).rounded_rectangle((30, 10, 110, 130), radius=10,
                                           fill=(40+variant, 110, 130, 255))
    out = io.BytesIO()
    image.save(out, format='PNG')
    return out.getvalue()


def install(monkeypatch, tmp_path, *, count=1, opaque=False, reviewed=True,
            role='product_packshot', corrupt=False, usable=True):
    rows, paths = [], {}
    for i in range(count):
        raw = image_bytes(opaque=opaque, variant=i)
        digest = hashlib.sha256(raw).hexdigest()
        row = Asset(id='official-'+digest[:20], product=PRODUCT, source_id='source',
            url='/api/assets/private', local_path='private/source/path',
            verified=True, provenance='huawei_official', sha256=digest,
            visual_role=role, semantic_basis='pixel_review_sha256' if reviewed else 'title_only',
            captured_at='2026-10-23')
        path = tmp_path / (str(i)+'.png')
        path.write_bytes(b'changed' if corrupt else raw)
        rows.append(row)
        paths[row.id] = path
    class Provider:
        def _add_official_assets(self, bundle, product, query):
            bundle.assets.extend(rows)
            bundle.sources.extend([
                Source(id='source', title='官方产品页', provider='huawei_official',
                       url='https://consumer.huawei.com/cn/phones/mate90/'),
                Source(id='unselected', title='unselected source'),
            ])
        async def get_asset(self, identifier):
            return paths.get(identifier)
    monkeypatch.setattr(module, 'LocalKnowledgeProvider', Provider)
    monkeypatch.setattr(module, 'review_asset_bytes', lambda *args: {
        'usable': usable, 'visual_role': role,
        'semantic_basis': 'pixel_review_sha256' if reviewed else 'title_only'})
    return rows


def collect(product=PRODUCT, existing=None):
    return asyncio.run(module.collect_product_subjects(product, existing or {}))


def test_real_alpha_subject_prepares_preserves_source_and_omits_private_locations(monkeypatch, tmp_path):
    row = install(monkeypatch, tmp_path)[0]
    assets, sources = collect()
    item = assets[row.id]
    assert item['prepared_subject'] and item['asset_kind'] == 'packshot'
    assert item['catalog_sha256'] == item['source_sha256'] == row.sha256
    assert item['sha256'] != row.sha256  # actual trimmed PNG, not catalog bytes
    assert item['source_id'] == row.source_id
    assert 'url' not in item and 'local_path' not in item
    assert 0 < item['alpha_fraction'] < 1
    assert item['pixel_budget']['effective_width'] == 81
    assert item['preparation']['operation']['backend'] == 'source_alpha'
    assert [source['id'] for source in sources] == ['source']
    assert sources[0]['captured_at'] == '2026-10-23'
    assert collect(existing=assets) == ({}, [])


def test_existing_unprepared_asset_is_prepared_under_original_id(monkeypatch, tmp_path):
    row = install(monkeypatch, tmp_path)[0]
    assets, _ = collect(existing={row.id: {'sha256': row.sha256}})
    assert assets[row.id]['prepared_subject']


def test_same_product_only_and_byte_hash_rechecked(monkeypatch, tmp_path):
    install(monkeypatch, tmp_path)
    assert collect(PRODUCT+' Pro') == ({}, [])
    install(monkeypatch, tmp_path, corrupt=True)
    assert collect() == ({}, [])


def test_opaque_packshot_never_falls_back_to_rectangular_original(monkeypatch, tmp_path):
    install(monkeypatch, tmp_path, opaque=True)
    assert collect() == ({}, [])


def test_full_kv_and_generic_overview_are_not_subjects_even_when_alpha(monkeypatch, tmp_path):
    for role in ['campaign_key_visual', 'product_overview', 'scene', 'store_photo']:
        install(monkeypatch, tmp_path, role=role)
        assert collect() == ({}, [])


def test_unreviewed_or_unusable_never_becomes_packshot(monkeypatch, tmp_path):
    install(monkeypatch, tmp_path, reviewed=False)
    assert collect() == ({}, [])
    install(monkeypatch, tmp_path, usable=False)
    assert collect() == ({}, [])


def test_at_most_three_distinct_catalog_subjects(monkeypatch, tmp_path):
    rows = install(monkeypatch, tmp_path, count=5)
    assets, sources = collect()
    assert len(assets) == 3 and len(sources) == 1
    # The same catalog digest under a different existing ID is still duplicate.
    other = {'other-id': {**assets[rows[0].id]}}
    after, _ = collect(existing=other)
    assert rows[0].id not in after and len(after) == 3


def test_second_pixel_review_cannot_disagree_with_catalog_role(monkeypatch, tmp_path):
    install(monkeypatch, tmp_path)
    monkeypatch.setattr(module, 'review_asset_bytes', lambda *args: {
        'usable': True, 'visual_role': 'campaign_key_visual', 'semantic_basis': 'pixel_review_sha256'})
    assert collect() == ({}, [])
