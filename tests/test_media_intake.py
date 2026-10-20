"""Behavioral coverage for real media boundaries and recoverable recognition."""
import asyncio
import io
import json
import sqlite3
import time
from types import SimpleNamespace
import wave

from fastapi import FastAPI
from fastapi.testclient import TestClient
import httpx
from PIL import Image
import pytest

from app import media_intake as media
from app import media_routes


def photo():
    output = io.BytesIO()
    exif = Image.Exif()
    exif[270] = 'private camera metadata'
    Image.new('RGB', (3200, 1000), '#aabbcc').save(output, format='JPEG', exif=exif)
    return output.getvalue()


def wav(seconds=1):
    output = io.BytesIO()
    with wave.open(output, 'wb') as audio:
        audio.setnchannels(1)
        audio.setsampwidth(2)
        audio.setframerate(8000)
        audio.writeframes(b'\0\0' * 8000 * seconds)
    return output.getvalue()


def config(**changes):
    return SimpleNamespace(image_review_model='', image_api_key='', image_base_url='https://vision.example/v1',
                           **changes)


def test_photo_is_oriented_bounded_and_metadata_is_removed():
    raw, mime, suffix = media.normalize_photo(photo())
    with Image.open(io.BytesIO(raw)) as image:
        assert image.size == (2400, 750)
        assert not image.getexif()
    assert mime == 'image/jpeg' and suffix == '.jpg'


def test_photo_disguised_svg_and_empty_audio_are_rejected():
    with pytest.raises(media.MediaInputError):
        media.normalize_photo(b'<svg onload="alert(1)"></svg>')
    with pytest.raises(media.MediaInputError):
        media.validate_audio(b'')


def test_audio_detects_content_without_trusting_uploaded_filename():
    assert media.validate_audio(wav())[1:] == ('audio/wav', '.wav')
    assert media.validate_audio(b'\x1aE\xdf\xa3webm-audio')[1:] == ('audio/webm', '.webm')
    with pytest.raises(media.MediaInputError, match='两分钟'):
        media.validate_audio(wav(121))


def test_visual_candidate_requires_visible_text_and_no_parameter_conversion():
    observed = media.parse_observations(json.dumps({
        'caption': '桌上摆着电脑', 'visible_text': ['HUAWEI MateBook 14'],
        'product_candidates': ['MateBook 14', 'MateBook Pro 32GB'], 'scene_hints': ['门店陈列'],
        'memory': '32 GB',
    }))
    assert observed['product_candidates'] == ['MateBook 14']
    assert 'memory' not in observed


def test_unconfigured_photo_keeps_real_preview_and_empty_observations(tmp_path):
    store = media.MediaStore(tmp_path)
    result = asyncio.run(media.process_media(photo(), 'photo', config(), store))
    assert result['status'] == 'needs_configuration'
    assert result['observations']['caption'] == ''
    assert result['observations']['product_candidates'] == []
    saved = store.get(result['id'])
    assert saved and saved[0].read_bytes().startswith(b'\xff\xd8')
    assert saved[2] == result


def test_upstream_failure_is_recoverable_and_does_not_leak_internal_error(monkeypatch, tmp_path):
    async def fail(*args):
        raise media.MediaServiceError('service_unavailable', '请重试或补充文字')
    monkeypatch.setattr(media, 'analyze_photo', fail)
    result = asyncio.run(media.process_media(photo(), 'photo', config(), media.MediaStore(tmp_path)))
    assert result['status'] == 'retryable'
    assert result['error']['code'] == 'service_unavailable'
    assert result['preview_url'].startswith('/api/media/')


def test_real_vision_contract_and_model_text(monkeypatch, tmp_path):
    captured = {}
    async def request(url, key, **kwargs):
        captured.update(url=url, key=key, body=kwargs['json'])
        return {'choices': [{'message': {'content': json.dumps({
            'caption': '门店体验桌', 'visible_text': [], 'product_candidates': [], 'scene_hints': ['到店体验'],
        })}}]}
    monkeypatch.setattr(media, 'request_json', request)
    settings = SimpleNamespace(image_review_model='vision-test', image_api_key='CANARY', image_base_url='https://vision.example/v1')
    result = asyncio.run(media.process_media(photo(), 'photo', settings, media.MediaStore(tmp_path)))
    assert captured['url'] == 'https://vision.example/v1/chat/completions'
    image_url = captured['body']['messages'][1]['content'][1]['image_url']['url']
    assert image_url.startswith('data:image/jpeg;base64,')
    assert result['observations']['caption'] == '门店体验桌'
    assert result['questions'] and 'CANARY' not in json.dumps(result)


def test_asr_uses_multipart_and_returns_actual_transcript(monkeypatch, tmp_path):
    captured = {}
    async def request(url, key, **kwargs):
        captured.update(url=url, **kwargs)
        return {'text': '给准备开学的同学做一篇手机电脑协同的内容'}
    monkeypatch.setattr(media, 'request_json', request)
    settings = SimpleNamespace(asr_base_url='http://127.0.0.1:8900/v1', asr_model='Qwen3-ASR-1.7B', asr_api_key='')
    result = asyncio.run(media.process_media(wav(), 'audio', settings, media.MediaStore(tmp_path)))
    assert result['status'] == 'ready' and result['transcript'].startswith('给准备开学')
    assert captured['url'].endswith('/v1/audio/transcriptions')
    assert captured['data']['language'] == 'zh'
    assert captured['files']['file'][0] == 'recording.wav'


def test_truncated_vision_is_retried_once_with_smaller_observation_budget(monkeypatch):
    requests = []
    async def request(*args, **kwargs):
        requests.append(kwargs['json'])
        if len(requests) == 1:
            return {'choices': [{'finish_reason': 'length', 'message': {'content': '{"caption": "unfinished'}}]}
        return {'choices': [{'finish_reason': 'stop', 'message': {'content': '{"caption":"店内体验桌","visible_text":[],"product_candidates":[],"scene_hints":[]}'}}]}
    monkeypatch.setattr(media, 'request_json', request)
    settings = SimpleNamespace(image_review_model='test', image_api_key='test', image_base_url='https://vision.example/v1')
    result = asyncio.run(media.analyze_photo(b'image', settings))
    assert result['caption'] == '店内体验桌'
    assert len(requests) == 2
    assert '最多3段' in requests[1]['messages'][0]['content']


def test_empty_asr_never_becomes_fabricated_transcript(monkeypatch, tmp_path):
    async def request(*args, **kwargs):
        return {'text': '  '}
    monkeypatch.setattr(media, 'request_json', request)
    settings = SimpleNamespace(asr_base_url='http://localhost:8900', asr_model='test', asr_api_key='')
    result = asyncio.run(media.process_media(wav(), 'audio', settings, media.MediaStore(tmp_path)))
    assert result['status'] == 'retryable' and result['transcript'] == ''
    assert result['error']['code'] == 'empty_audio'


def test_store_rejects_traversal_and_expired_preview(tmp_path):
    store = media.MediaStore(tmp_path)
    identifier = store.put(b'bytes', 'image/jpeg', '.jpg')
    assert store.get('../index.sqlite') is None
    with store.connect() as db:
        db.execute('UPDATE media SET created=? WHERE id=?', (time.time() - 90000, identifier))
    assert store.get(identifier) is None
    store.prune()
    assert not (tmp_path / (identifier + '.jpg')).exists()


@pytest.fixture
def client(monkeypatch, tmp_path):
    media_routes._requests.clear()
    store = media.MediaStore(tmp_path)
    monkeypatch.setattr(media_routes, 'media_store', lambda: store)
    monkeypatch.setattr(media_routes, 'settings', config())
    application = FastAPI()
    application.include_router(media_routes.router)
    return TestClient(application)


def test_upload_preview_and_private_response_headers(client):
    response = client.post('/api/media/intake', data={'kind': 'photo'}, files={'file': ('wrong.svg', photo(), 'image/svg+xml')})
    assert response.status_code == 200
    result = response.json()
    preview = client.get(result['preview_url'])
    assert preview.status_code == 200 and preview.headers['content-type'] == 'image/jpeg'
    assert preview.headers['cache-control'] == 'private, no-store'
    assert preview.headers['x-content-type-options'] == 'nosniff'
    assert client.get('/api/media/not-a-real-id').status_code == 404


def test_route_rejects_wrong_kind_and_oversized_body(client):
    response = client.post('/api/media/intake', data={'kind': 'pdf'}, files={'file': ('capture.jpg', photo(), 'image/jpeg')})
    assert response.status_code == 422
    response = client.post('/api/media/intake', content=b'x' * (media.MAX_UPLOAD_BYTES + 65537),
                           headers={'content-type': 'multipart/form-data; boundary=test'})
    assert response.status_code == 413


def test_gateway_error_body_is_never_returned(monkeypatch):
    client_type = httpx.AsyncClient
    transport = httpx.MockTransport(lambda request: httpx.Response(401, json={'error': 'CANARY_SECRET'}))
    monkeypatch.setattr(media.httpx, 'AsyncClient', lambda **kwargs: client_type(transport=transport, **kwargs))
    with pytest.raises(media.MediaServiceError) as error:
        asyncio.run(media.request_json('https://vision.example/v1/chat/completions', 'test', json={}))
    assert 'CANARY_SECRET' not in str(error.value)
