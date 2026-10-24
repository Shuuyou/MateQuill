import asyncio
import base64
import hashlib
import io
import json

import httpx
import pytest
from PIL import Image

from app import design_image_tools as tools


def asset(color='red', provenance='huawei_official'):
    stream = io.BytesIO()
    Image.new('RGB', (64, 64), color).save(stream, 'PNG')
    raw = stream.getvalue()
    return {'verified': True, 'provenance': provenance,
        'sha256': hashlib.sha256(raw).hexdigest(),
        'data_url': 'data:image/png;base64,'+base64.b64encode(raw).decode()}


def setup(monkeypatch, *, fail=False):
    tools._ASSET_OBSERVATION_CACHE.clear()
    requests = []
    actual = httpx.AsyncClient

    def handler(request):
        requests.append(request)
        if fail:
            return httpx.Response(503)
        payload = {'assets': [{'id': 'a0', 'view': 'front', 'framing': '正面产品', 'photo': False}]}
        return httpx.Response(200, json={'choices': [{'message': {'content': json.dumps(payload)}}]})

    monkeypatch.setattr(httpx, 'AsyncClient', lambda **kw: actual(transport=httpx.MockTransport(handler), **kw))
    return requests


def describe(assets, model='vision', key='secret', endpoint='https://synthetic.example/v1'):
    return asyncio.run(tools.describe_design_assets(assets, endpoint, key, model))


def test_identical_verified_official_pixels_reuse_completed_observation(monkeypatch):
    requests = setup(monkeypatch)
    assets = {'actual-id': asset()}
    first = describe(assets)
    first['actual-id']['view'] = 'changed-by-caller'
    assert describe(assets)['actual-id']['view'] == 'front'
    assert len(requests) == 1


@pytest.mark.parametrize('change', ['pixels', 'model', 'key', 'endpoint', 'asset_id'])
def test_observation_cache_rechecks_changed_binding(monkeypatch, change):
    requests = setup(monkeypatch)
    assets, options = {'actual-id': asset()}, {}
    describe(assets)
    if change == 'pixels': assets = {'actual-id': asset('blue')}
    elif change == 'asset_id': assets = {'other-id': asset()}
    elif change == 'endpoint': options[change] = 'https://other.example/v1'
    else: options[change] = 'changed'
    assert describe(assets, **options)
    assert len(requests) == 2


@pytest.mark.parametrize('kind', ['store_upload', 'user_photo', 'unknown'])
def test_private_or_unknown_materials_never_cross_task_cache(monkeypatch, kind):
    requests = setup(monkeypatch)
    assets = {'actual-id': asset(provenance=kind)}
    describe(assets)
    describe(assets)
    assert len(requests) == 2


def test_changed_bytes_with_old_claimed_hash_never_uses_cached_observation(monkeypatch):
    requests = setup(monkeypatch)
    original = asset()
    describe({'actual-id': original})
    modified = {**asset('blue'), 'sha256': original['sha256']}
    describe({'actual-id': modified})
    describe({'actual-id': modified})
    assert len(requests) == 3


def test_expired_observation_is_rechecked(monkeypatch):
    requests = setup(monkeypatch)
    now = [100.0]
    monkeypatch.setattr(tools.time, 'monotonic', lambda: now[0])
    describe({'actual-id': asset()})
    now[0] += tools._OBSERVATION_TTL + 1
    describe({'actual-id': asset()})
    assert len(requests) == 2


def test_failed_observation_never_caches_success(monkeypatch):
    requests = setup(monkeypatch, fail=True)
    assert describe({'actual-id': asset()}) == {}
    assert describe({'actual-id': asset()}) == {}
    assert len(requests) == 2 and not tools._ASSET_OBSERVATION_CACHE
