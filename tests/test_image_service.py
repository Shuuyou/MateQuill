import asyncio
import base64
import io
import json

import httpx
from PIL import Image
import pytest

from app.image_service import ImageService, ImageServiceError


def image_data(size=(64, 96)):
    stream = io.BytesIO()
    Image.new('RGB', size, '#e9eee3').save(stream, format='PNG')
    return stream.getvalue()


def service():
    return ImageService('https://generation.example/v1', 'CANARY_SECRET', 'test-image-model')


def transport(monkeypatch, handler):
    original = httpx.AsyncClient
    monkeypatch.setattr(httpx, 'AsyncClient', lambda **kwargs: original(
        transport=httpx.MockTransport(handler), **kwargs))


def test_model_listing_does_not_claim_generation_support(monkeypatch):
    transport(monkeypatch, lambda request: httpx.Response(200, json={'data': [
        {'id': 'chat-model', 'secret': 'CANARY_SECRET'}, {'id': 'image-model'},
        {'id': '<script>bad</script>'}, {'wrong': True}]}))
    assert asyncio.run(service().list_models()) == [
        {'id': 'chat-model', 'capability_verified': False},
        {'id': 'image-model', 'capability_verified': False}]


@pytest.mark.parametrize('status', [401, 403, 404, 429, 500])
def test_upstream_errors_never_echo_secret_or_url(monkeypatch, status):
    transport(monkeypatch, lambda request: httpx.Response(status, json={
        'error': {'message': 'CANARY_SECRET https://private.example/?secret=CANARY_SECRET'}}))
    with pytest.raises(ImageServiceError) as caught:
        asyncio.run(service().generate_background('quiet classroom', 64, 96))
    assert 'CANARY_SECRET' not in str(caught.value)
    assert 'private.example' not in str(caught.value)
    assert caught.value.code == status


def test_base64_generation_locks_background_scope_and_validates_pixels(monkeypatch):
    requests = []
    raw = image_data()
    def handler(request):
        requests.append(request)
        return httpx.Response(200, json={'data': [{'b64_json': base64.b64encode(raw).decode()}]},
                              headers={'x-request-id': 'req-123'})
    transport(monkeypatch, handler)
    artifact = asyncio.run(service().generate_background('sage desk light', 64, 96))
    assert artifact.data == raw
    assert (artifact.width, artifact.height, artifact.mime) == (64, 96, 'image/png')
    assert artifact.request_id == 'req-123'
    assert artifact.provenance == 'ai_generated_background'
    body = json.loads(requests[0].content)
    assert body['size'] == '64x96'
    assert 'NO products' in body['prompt']
    assert 'logos' in body['prompt']
    assert requests[0].url.path == '/v1/images/generations'


def test_one_parameter_compatibility_retry(monkeypatch):
    requests = []
    def handler(request):
        body = json.loads(request.content)
        requests.append(body)
        if 'response_format' in body:
            return httpx.Response(400, json={'error': {'message': 'Unsupported response_format'}})
        return httpx.Response(200, json={'data': [{'b64_json': base64.b64encode(image_data()).decode()}]})
    transport(monkeypatch, handler)
    asyncio.run(service().generate_background('empty desk', 64, 96))
    assert len(requests) == 2
    assert 'response_format' not in requests[1]


def test_arbitrary_400_is_not_retried(monkeypatch):
    calls = []
    def handler(request):
        calls.append(request)
        return httpx.Response(400, json={'error': {'message': 'unsupported size'}})
    transport(monkeypatch, handler)
    with pytest.raises(ImageServiceError):
        asyncio.run(service().generate_background('empty desk', 64, 96))
    assert len(calls) == 1


@pytest.mark.parametrize('value', ['bm90IGFuIGltYWdl', '%%%', base64.b64encode(b'\x89PNG\r\n\x1a\nwrong').decode()])
def test_invalid_image_is_rejected(monkeypatch, value):
    transport(monkeypatch, lambda request: httpx.Response(200, json={'data': [{'b64_json': value}]}))
    with pytest.raises(ImageServiceError):
        asyncio.run(service().generate_background('empty desk', 64, 96))


@pytest.mark.parametrize('url', ['https://127.0.0.1/p.png', 'http://cdn.example/p.png',
                               'https://169.254.169.254/a', 'https://[::1]/a',
                               'https://user:pass@cdn.example/a', 'https://cdn.example:8443/a'])
def test_unsafe_url_rejected_without_downloading(monkeypatch, url):
    requests = []
    def handler(request):
        requests.append(request)
        return httpx.Response(200, json={'data': [{'url': url}]})
    transport(monkeypatch, handler)
    with pytest.raises(ImageServiceError, match='HTTPS'):
        asyncio.run(service().generate_background('empty', 64, 96))
    assert len(requests) == 1


def test_dns_rebinding_is_prevented_by_ip_pinning_and_no_credentials(monkeypatch):
    requests = []
    raw = image_data()
    def handler(request):
        requests.append(request)
        if request.method == 'POST':
            return httpx.Response(200, json={'data': [{'url': 'https://cdn.example/a.png?token=private'}]})
        assert request.url.host == '93.184.216.34'
        assert request.headers['host'] == 'cdn.example'
        assert request.extensions['sni_hostname'] == 'cdn.example'
        assert 'authorization' not in request.headers
        return httpx.Response(200, content=raw)
    transport(monkeypatch, handler)
    instance = service()
    async def resolver(host):
        assert host == 'cdn.example'
        return ['93.184.216.34']
    instance._resolve_public = resolver
    assert asyncio.run(instance.generate_background('empty', 64, 96)).data == raw
    assert len(requests) == 2


def test_download_redirects_are_not_followed(monkeypatch):
    requests = []
    def handler(request):
        requests.append(request)
        if request.method == 'POST':
            return httpx.Response(200, json={'data': [{'url': 'https://cdn.example/a'}]})
        return httpx.Response(302, headers={'location': 'https://127.0.0.1/secret'})
    transport(monkeypatch, handler)
    instance = service()
    async def resolver(host):
        return ['93.184.216.34']
    instance._resolve_public = resolver
    with pytest.raises(ImageServiceError, match='跳转'):
        asyncio.run(instance.generate_background('empty', 64, 96))
    assert len(requests) == 2


def test_private_dns_answer_rejected(monkeypatch):
    async def private_dns(*args, **kwargs):
        return [(2, 1, 6, '', ('10.0.0.1', 443))]
    async def run():
        monkeypatch.setattr(asyncio.get_running_loop(), 'getaddrinfo', private_dns)
        with pytest.raises(ImageServiceError, match='公网'):
            await service()._resolve_public('cdn.example')
    asyncio.run(run())


def test_actual_dimensions_are_not_misrepresented(monkeypatch):
    transport(monkeypatch, lambda request: httpx.Response(200, json={'data': [
        {'b64_json': base64.b64encode(image_data((128, 128))).decode()}]}))
    artifact = asyncio.run(service().generate_background('empty', 64, 96))
    assert (artifact.width, artifact.height) == (128, 128)
    assert (artifact.requested_width, artifact.requested_height) == (64, 96)


def test_image_limits_and_non_image_chat_response(monkeypatch):
    transport(monkeypatch, lambda request: httpx.Response(200, json={'choices': [{'message': {'content': 'image'}}]}))
    with pytest.raises(ImageServiceError, match='协议'):
        asyncio.run(service().generate_background('empty', 64, 96))
    with pytest.raises(ImageServiceError):
        service()._validate_image(image_data((16, 16)))
    instance = service()
    instance.max_image_bytes = 10
    with pytest.raises(ImageServiceError):
        instance._validate_image(image_data())
