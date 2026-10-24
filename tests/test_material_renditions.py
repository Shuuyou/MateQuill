import hashlib
import io

import pytest
from PIL import Image

from app.design.material_renditions import register_verified_rendition, resolve_verified_rendition


def fixture(tmp_path, size=(1920, 1920)):
    row = {'spu_id': '123', 'source_sha256': 'a'*64}
    asset = {'spu_id': '123', 'sku_id': '456', 'scope': 'sku_gallery', 'sha256': 'b'*64,
             'image_url': 'https://res.vmallres.com/pimages/800_800_EXACT.png'}
    high = 'https://res.vmallres.com/pimages/1920_1920_EXACT.png'
    path = tmp_path/'runtime/actual.png'; path.parent.mkdir();Image.new('RGBA', size).save(path)
    sha = hashlib.sha256(path.read_bytes()).hexdigest()
    return row, asset, high, path, sha


def test_real_local_sha_dimension_and_exact_source_binding(tmp_path):
    row, asset, high, path, sha = fixture(tmp_path)
    record = register_verified_rendition(row, asset, image_url=high, path=path, expected_sha256=sha, root=tmp_path)
    resolved = resolve_verified_rendition(row, asset, root=tmp_path)
    assert record['width'] == resolved['width'] == 1920
    assert resolved['sha256'] == sha and resolved['image_url'] == high
    assert resolved['rendition_source']['original_url'] == asset['image_url']
    assert 'semantic_basis' not in resolved and 'verified' not in resolved
    assert asset['image_url'].endswith('800_800_EXACT.png')


def test_changed_page_sku_source_hash_or_filename_never_reuses_sidecar(tmp_path):
    row, asset, high, path, sha = fixture(tmp_path)
    register_verified_rendition(row, asset, image_url=high, path=path, expected_sha256=sha, root=tmp_path)
    for changed in [{**asset, 'sku_id': '457'}, {**asset, 'sha256': 'c'*64},
                    {**asset, 'image_url': asset['image_url'].replace('EXACT', 'OTHER')}]:
        assert resolve_verified_rendition(row, changed, root=tmp_path) == changed
    assert resolve_verified_rendition({**row, 'source_sha256': 'd'*64}, asset, root=tmp_path) == asset
    path.write_bytes(b'changed')
    assert resolve_verified_rendition(row, asset, root=tmp_path) == asset


@pytest.mark.parametrize('url', ['https://res.vmallres.com/pimages/1920_1920_OTHER.png',
    'https://res1.vmallres.com/pimages/1920_1920_EXACT.png',
    'https://res.vmallres.com/pimages/maxSize3680_EXACT.png',
    'http://res.vmallres.com/pimages/1920_1920_EXACT.png',
    'https://evil.example/pimages/1920_1920_EXACT.png'])
def test_no_guessing_host_filename_or_max_size(tmp_path, url):
    row, asset, _, path, sha = fixture(tmp_path)
    with pytest.raises(ValueError, match='exact_800_to_1920'):
        register_verified_rendition(row, asset, image_url=url, path=path, expected_sha256=sha, root=tmp_path)


def test_declared_filename_does_not_prove_pixels(tmp_path):
    row, asset, high, path, sha = fixture(tmp_path, size=(800, 800))
    with pytest.raises(ValueError, match='actual_dimensions'):
        register_verified_rendition(row, asset, image_url=high, path=path, expected_sha256=sha, root=tmp_path)
    with pytest.raises(ValueError, match='sha_mismatch'):
        register_verified_rendition(row, asset, image_url=high, path=path, expected_sha256='e'*64, root=tmp_path)


def test_detail_and_missing_sku_cannot_get_an_invented_variant(tmp_path):
    row, asset, high, path, sha = fixture(tmp_path)
    for changed in [{**asset, 'scope': 'product_detail'}, {**asset, 'sku_id': None}, {**asset, 'spu_id': '124'}]:
        with pytest.raises(ValueError, match='source_binding'):
            register_verified_rendition(row, changed, image_url=high, path=path, expected_sha256=sha, root=tmp_path)
