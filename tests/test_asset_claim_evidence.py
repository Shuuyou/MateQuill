from copy import deepcopy
import hashlib
import json

import pytest

from app import visual_assets
from app.asset_claim_evidence import build_asset_claim_evidence
from app.models import Asset, EvidenceBundle, Source


@pytest.fixture
def inputs(tmp_path, monkeypatch):
    raw = b'locally captured immutable asset bytes'
    path = tmp_path / 'authorized.bin'
    path.write_bytes(raw)
    sha = hashlib.sha256(raw).hexdigest()
    registry = tmp_path / 'observations.json'
    registry.write_text(json.dumps({sha: {'observed_title': '绿色机身斜置，圆形后摄',
        'semantic_tags': ['背面'], 'visual_role': 'product_overview', 'usable': True}}), encoding='utf-8')
    monkeypatch.setattr(visual_assets, 'REVIEW_PATH', registry)
    bundle = EvidenceBundle(product='Mate80', assets=[Asset(id='a', product='Mate80',
        source_id='s', url='https://unused.invalid/a', local_path=str(path),
        title='不使用标题作为外观证据', original_title='official.jpg',
        sha256=sha, verified=True, provenance='huawei_official')],
        sources=[Source(id='s', title='授权产品目录')])
    draft = {'images': [{'asset_id': 'a', 'caption': '产品资料图：Mate80'}],
             'creative_plan': {'cards': [{'id': 'c1', 'asset_ids': ['a'], 'body': '绿色机身'},
                                        {'id': 'other', 'asset_ids': ['missing'], 'body': '另一张图'}]}}
    return draft, bundle, registry, path


def test_identity_and_actual_registered_pixel_observation_have_separate_scope(inputs):
    draft, bundle, _, _ = inputs
    original = deepcopy(bundle.model_dump())
    rows = build_asset_claim_evidence(draft, bundle)
    assert [row['kind'] for row in rows] == ['asset_identity', 'asset_appearance']
    assert rows[0]['text'] == 'Mate80'
    assert rows[1]['text'] == '绿色机身斜置，圆形后摄'
    for row in rows:
        assert row['allowed_surfaces'] == ['image:0.caption', 'card:c1.body']
        assert row['asset_id'] == 'a' and row['source_id'] == 's'
        assert row['sha256'] == bundle.assets[0].sha256
        assert '性能' in row['claim_scope'] and '亲历' in row['claim_scope']
        assert 'local_path' not in row and 'url' not in row
    assert rows[0]['id'] != rows[1]['id']
    assert rows == build_asset_claim_evidence(draft, bundle.model_dump())
    assert bundle.model_dump() == original


@pytest.mark.parametrize('mutation', ['not_selected', 'unknown_asset', 'wrong_source', 'unverified',
                                     'empty_product', 'bad_sha', 'duplicate_id'])
def test_no_identity_from_unbound_or_unauthorized_metadata(inputs, mutation):
    draft, bundle, _, _ = inputs
    if mutation == 'not_selected':
        draft = {'images': []}
    elif mutation == 'unknown_asset':
        draft = {'images': [{'asset_id': 'unbound', 'caption': '产品图'}]}
    elif mutation == 'wrong_source':
        bundle.assets[0].source_id = 'missing'
    elif mutation == 'unverified':
        bundle.assets[0].verified = False
    elif mutation == 'empty_product':
        bundle.assets[0].product = ''
    elif mutation == 'bad_sha':
        bundle.assets[0].sha256 = 'not-a-sha'
    else:
        bundle.assets.append(bundle.assets[0].model_copy())
    assert build_asset_claim_evidence(draft, bundle) == []


@pytest.mark.parametrize('mutation', ['changed_bytes', 'missing_path', 'remote_path', 'unc_path', 'no_path',
                                     'title_only', 'unusable', 'malformed', 'invalid_registry'])
def test_no_appearance_without_current_hash_matching_registered_observation(inputs, mutation):
    draft, bundle, registry, path = inputs
    if mutation == 'changed_bytes':
        path.write_bytes(b'changed after retrieval')
    elif mutation == 'missing_path':
        bundle.assets[0].local_path = str(path.parent / 'missing.bin')
    elif mutation == 'remote_path':
        bundle.assets[0].local_path = 'https://unused.invalid/not-fetched'
    elif mutation == 'unc_path':
        bundle.assets[0].local_path = '\\\\unused.invalid\\share\\not-fetched'
    elif mutation == 'no_path':
        bundle.assets[0].local_path = ''
    elif mutation == 'title_only':
        registry.write_text('{}', encoding='utf-8')
    elif mutation == 'invalid_registry':
        registry.write_text('{', encoding='utf-8')
    else:
        records = json.loads(registry.read_text(encoding='utf-8'))
        record = records[bundle.assets[0].sha256]
        if mutation == 'unusable':
            record['usable'] = False
        else:
            del record['observed_title']
        registry.write_text(json.dumps(records), encoding='utf-8')
    assert [row['kind'] for row in build_asset_claim_evidence(draft, bundle)] == ['asset_identity']


def test_server_asset_path_only_never_user_filename_fallback(inputs):
    draft, bundle, _, path = inputs
    bundle.assets[0].local_path = ''
    draft['images'][0].update(local_path=str(path), filename=str(path), sha256=bundle.assets[0].sha256)
    bundle.user_materials = [{'filename': str(path), 'local_path': str(path)}]
    assert [row['kind'] for row in build_asset_claim_evidence(draft, bundle)] == ['asset_identity']


def test_assets_cannot_supply_records_outside_their_selected_surfaces(inputs):
    draft, bundle, _, _ = inputs
    second = bundle.assets[0].model_copy(update={'id': 'b', 'product': 'OtherModel'})
    bundle.assets.append(second)
    draft['images'].append({'asset_id': 'b', 'caption': '另一型号资料图'})
    rows = build_asset_claim_evidence(draft, bundle)
    assert {tuple(row['allowed_surfaces']) for row in rows if row['asset_id'] == 'a'} == {
        ('image:0.caption', 'card:c1.body')}
    assert {tuple(row['allowed_surfaces']) for row in rows if row['asset_id'] == 'b'} == {('image:1.caption',)}
    assert len({row['id'] for row in rows}) == 4
    assert not {row['kind'] for row in rows} & {'spec', 'performance', 'experience', 'actual_record', 'explicit_outcome'}
