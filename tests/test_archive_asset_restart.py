"""Persisted archive image IDs survive a provider restart, within trusted roots."""
import asyncio
import hashlib
from pathlib import Path

import pytest
from PIL import Image

from app.demo import DemoProvider
from app.local_knowledge import LocalKnowledgeProvider


def archive(tmp_path, filename='original.png'):
    root = tmp_path/'knowledge'
    folder = root/'P1-产品参数库'/'TestBook'
    (folder/'images').mkdir(parents=True)
    (folder/'产品事实.md').write_text('# TestBook\n\n## 内存\n\n容量\n16GB\n', encoding='utf8')
    image = folder/'images'/filename
    Image.new('RGB', (64, 48), '#c8d8c5').save(image, 'PNG')
    (folder/'图片索引.csv').write_text('产品型号,文件名,官网图片说明\nTestBook,'+filename+',产品外观\n', encoding='utf-8-sig')
    return root, folder, image


def local(root, tmp_path):
    return LocalKnowledgeProvider(root, tmp_path/'no-products.json', tmp_path/'no-official-assets.json')


@pytest.mark.parametrize('formal', [False, True])
def test_retrieved_archive_image_survives_new_provider_without_retrieval(tmp_path, formal):
    root, folder, image = archive(tmp_path)
    build = (lambda: local(root, tmp_path)) if formal else (lambda: DemoProvider(root))
    first = build()
    bundle = asyncio.run(first.retrieve('TestBook'))
    assert bundle.assets
    identifier = bundle.assets[0].id
    assert identifier == 'demo-'+hashlib.sha256(str(image).encode()).hexdigest()[:16]
    expected = image.read_bytes()
    restarted = build()
    assert not restarted._assets
    recovered = asyncio.run(restarted.get_asset(identifier))
    assert recovered == image and recovered.read_bytes() == expected
    image.write_bytes(b'changed after this provider recovered the image')
    assert asyncio.run(restarted.get_asset(identifier)) is None


def test_archive_index_cannot_escape_root_or_serve_non_images(tmp_path):
    root, folder, image = archive(tmp_path)
    outside = tmp_path/'outside.png'
    Image.new('RGB', (8, 8), 'white').save(outside)
    text = folder/'images'/'private.txt'
    text.write_text('Not an image', encoding='utf8')
    escaped = folder/'images'/'../../../../outside.png'
    # The legacy ID can be computed, but its presence in a CSV does not grant
    # permission to expose a file outside the trusted archive root.
    assert escaped.resolve() == outside.resolve()
    (folder/'图片索引.csv').write_text('产品型号,文件名,官网图片说明\n'
        'TestBook,../../../../outside.png,产品外观\nTestBook,private.txt,产品外观\n', encoding='utf8')
    for factory in (lambda: DemoProvider(root), lambda: local(root, tmp_path)):
        provider = factory()
        assert asyncio.run(provider.retrieve('TestBook')).assets == []
        for path in (escaped, text):
            identifier = 'demo-'+hashlib.sha256(str(path).encode()).hexdigest()[:16]
            assert asyncio.run(factory().get_asset(identifier)) is None


def test_unknown_id_and_unindexed_local_file_are_not_disclosed(tmp_path):
    root, folder, image = archive(tmp_path)
    hidden = folder/'images'/'unindexed.png'
    Image.new('RGB', (8, 8), 'black').save(hidden)
    provider = DemoProvider(root)
    identifier = 'demo-'+hashlib.sha256(str(hidden).encode()).hexdigest()[:16]
    assert asyncio.run(provider.get_asset(identifier)) is None
    for value in ('../outside.png', 'demo-not-a-digest', 'official-'+'a'*20):
        assert asyncio.run(provider.get_asset(value)) is None


def test_symlinked_archive_image_cannot_point_outside_root(tmp_path):
    root, folder, image = archive(tmp_path)
    outside = tmp_path/'outside.png'
    Image.new('RGB', (8, 8), 'white').save(outside)
    link = folder/'images'/'linked.png'
    try:
        link.symlink_to(outside)
    except OSError:
        pytest.skip('Creating symbolic links requires platform permission')
    (folder/'图片索引.csv').write_text('产品型号,文件名,官网图片说明\nTestBook,linked.png,产品外观\n', encoding='utf8')
    provider = DemoProvider(root)
    identifier = 'demo-'+hashlib.sha256(str(link).encode()).hexdigest()[:16]
    assert asyncio.run(provider.get_asset(identifier)) is None
    assert asyncio.run(provider.retrieve('TestBook')).assets == []
