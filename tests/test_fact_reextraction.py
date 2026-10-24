from copy import deepcopy
import hashlib

import pytest

from scripts.reextract_product_facts import reextract_catalog, VERSION


def snapshot_document(tmp_path):
    # Standalone fixture: a fresh clone needs no private runtime snapshots.
    raw = ('<h1>SnapshotDevice</h1><div class="large-accordion__item">'
        '<h2 class="large-accordion__title">电池</h2>'
        '<div class="large-accordion__content"><p>容量</p><p>约70Wh</p>'
        '<p>*典型值，实际使用情况可能不同。</p></div></div>').encode()
    (tmp_path / 'snapshot.html').write_bytes(raw)
    url = 'https://consumer.huawei.com/cn/laptops/test/specs/'
    product = {'name': 'SnapshotDevice', 'spec_url': url,
        'captured_at': '2026-10-21T00:00:00+00:00',
        'sources': [{'role': 'spec', 'url': url, 'local_path': 'snapshot.html',
                     'sha256': hashlib.sha256(raw).hexdigest()}],
        'text': '# SnapshotDevice\n\n## 电池\n\n容量\n约70Wh\n*典型值，实际使用情况可能不同。',
        'facts': [{'id': 'legacy', 'value': 'retained in the previous catalog version'}]}
    return {'schema_version': 1, 'products': [product]}


def test_verified_reextraction_keeps_capture_time_source_text_and_previous_version(tmp_path):
    original = snapshot_document(tmp_path)
    previous = deepcopy(original)
    result, changes = reextract_catalog(original, root=tmp_path)
    product = result['products'][0]
    assert original == previous
    assert product['text'] == previous['products'][0]['text']
    assert product['captured_at'] == previous['products'][0]['captured_at']
    assert product['sources'] == previous['products'][0]['sources']
    assert product['fact_extraction']['version'] == VERSION
    assert product['fact_extraction']['source_sha256'] == hashlib.sha256((tmp_path / 'snapshot.html').read_bytes()).hexdigest()
    assert changes[0]['retired_fact_ids'] == ['legacy']
    assert all(f['value'] in f['quote'] and f['quote'] in product['text'] for f in product['facts'])
    repeated, changes = reextract_catalog(result, root=tmp_path)
    assert repeated == result
    assert changes[0]['changed'] is False


def test_reextraction_rejects_modified_raw_snapshot(tmp_path):
    document = snapshot_document(tmp_path)
    (tmp_path / 'snapshot.html').write_bytes(b'modified')
    with pytest.raises(ValueError, match='hash mismatch'):
        reextract_catalog(document, root=tmp_path)


def test_reextraction_rejects_unreviewed_source_text_changes(tmp_path):
    document = snapshot_document(tmp_path)
    document['products'][0]['text'] += '\nUnreviewed new claim'
    with pytest.raises(ValueError, match='Source text changed'):
        reextract_catalog(document, root=tmp_path)


def test_reextraction_cannot_read_a_snapshot_outside_workspace(tmp_path):
    document = snapshot_document(tmp_path)
    next(s for s in document['products'][0]['sources'] if s['role'] == 'spec')['local_path'] = '../outside.html'
    with pytest.raises(ValueError, match='inside this workspace'):
        reextract_catalog(document, root=tmp_path)
