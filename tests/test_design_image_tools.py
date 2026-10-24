import asyncio
import base64
import io
import json

import httpx
import pytest
from PIL import Image
from app.image_service import ImageService, ImageServiceError
from app.design_image_tools import protect_regions, protection_mask, edit_mask, enhance_photo, read_image


def png(color='red', size=(64, 64)):
    stream = io.BytesIO()
    Image.new('RGB', size, color).save(stream, 'PNG')
    return stream.getvalue()


def mock(monkeypatch, handler):
    actual = httpx.AsyncClient
    monkeypatch.setattr(httpx, 'AsyncClient', lambda **kw: actual(transport=httpx.MockTransport(handler), **kw))


def service():
    return ImageService('https://images.example/v1', 'CANARY_SECRET', 'test-model')


def test_generate_full_concept_does_not_inherit_background_exclusions(monkeypatch):
    def handler(request):
        body = json.loads(request.content)
        assert body['prompt'] == 'full composed concept'
        return httpx.Response(200, json={'data': [{'b64_json': base64.b64encode(png()).decode()}]})
    mock(monkeypatch, handler)
    client = service()
    assert not any(item['verified'] for item in client.capabilities()['operations'])
    result = asyncio.run(client.generate('full composed concept', 64, 64))
    assert result.provenance == 'ai_generated_concept'
    assert client.capabilities()['operations'][0]['verified']
    assert not client.capabilities()['operations'][1]['verified']


def test_edit_sends_reference_and_mask_multipart_and_marks_only_success(monkeypatch):
    original, reference = png(), png('blue')
    mask = edit_mask(protection_mask((64, 64), [(0, 0, 32, 64)]))
    requests = []
    def handler(request):
        requests.append(request)
        assert request.url.path == '/v1/images/edits'
        assert 'multipart/form-data' in request.headers['content-type']
        assert b'name="image[]"' in request.content
        assert b'name="mask"' in request.content
        assert b'name="model"' in request.content
        return httpx.Response(200, json={'data': [{'b64_json': base64.b64encode(png('green')).decode()}]})
    mock(monkeypatch, handler)
    client = service()
    result = asyncio.run(client.edit('change scene', original, mask=mask, reference_images=(reference,)))
    assert result.provenance == 'ai_edited_candidate'
    assert {item['operation'] for item in client.capabilities()['operations'] if item['verified']} == {'edit', 'mask', 'reference'}
    assert len(requests) == 1


def test_edit_failure_no_retries_or_capability_claim(monkeypatch):
    calls = []
    def handler(request):
        calls.append(request)
        return httpx.Response(400, json={'error': {'message': 'CANARY_SECRET'}})
    mock(monkeypatch, handler)
    client = service()
    with pytest.raises(ImageServiceError) as error:
        asyncio.run(client.edit('change', png()))
    assert 'CANARY_SECRET' not in str(error.value)
    assert len(calls) == 1
    assert not any(item['verified'] for item in client.capabilities()['operations'])


def test_invalid_mask_never_calls_network(monkeypatch):
    mock(monkeypatch, lambda request: pytest.fail('network called'))
    with pytest.raises(ImageServiceError):
        asyncio.run(service().edit('change', png(), mask=png(size=(32, 32))))


def test_protected_product_pixels_are_restored_exactly():
    mask = protection_mask((64, 64), [(8, 12, 34, 50)])
    result = protect_regions(png('red'), png('blue'), mask)
    image = read_image(result['data'])
    assert image.getpixel((8, 12)) == (255, 0, 0, 255)
    assert image.getpixel((33, 49)) == (255, 0, 0, 255)
    assert image.getpixel((34, 49)) == (0, 0, 255, 255)
    assert result['protected_pixels'] == 26 * 38
    assert result['protected_pixels_preserved']
    assert not result['product_identity_verified']


def test_soft_masks_rejected_for_exact_preservation():
    with pytest.raises(ValueError):
        protect_regions(png(), png('blue'), png('#808080'))


def test_photo_crop_preserves_source_pixels_and_reports_operations():
    result = enhance_photo(png(), crop=(8, 8, 48, 56))
    assert (result['width'], result['height']) == (40, 48)
    assert result['operations'] == ['crop']
    assert read_image(result['data']).getpixel((0, 0)) == (255, 0, 0, 255)
    with pytest.raises(ValueError):
        enhance_photo(png(), crop=(0, 0, 65, 65))


def test_asset_description_uses_contact_sheet_and_maps_aliases(monkeypatch):
    from app.design_image_tools import describe_design_assets
    requests = []
    def handler(request):
        requests.append(request)
        body = json.loads(request.content)
        content = body['messages'][1]['content']
        assert content[0]['text'].endswith('a0, a1')
        encoded = content[1]['image_url']['url'].split(',', 1)[1]
        with Image.open(io.BytesIO(base64.b64decode(encoded))) as sheet:
            assert sheet.size == (640, 280)
            assert sheet.getpixel((160, 150))[0] > 240
            assert sheet.getpixel((480, 150))[2] > 240
        assert 'private-source' not in request.content.decode()
        response = {'assets': [{'id': 'a0', 'view': 'front', 'framing': '正面屏幕，居中产品', 'photo': False},
                               {'id': 'a1', 'view': 'rear', 'framing': '背面机身，浅色背景', 'photo': False}]}
        return httpx.Response(200, json={'choices': [{'message': {'content': json.dumps(response)}}]})
    mock(monkeypatch, handler)
    assets = {'server-id-1': {'data_url': 'data:image/png;base64,' + base64.b64encode(png('red')).decode(),
                            'url': 'https://private-source.example/secret'},
              'server-id-2': {'data_url': 'data:image/png;base64,' + base64.b64encode(png('blue')).decode()}}
    result = asyncio.run(describe_design_assets(assets, 'https://vision.example/v1', 'CANARY_SECRET', 'vl'))
    assert result['server-id-1']['view'] == 'front'
    assert result['server-id-2']['view'] == 'rear'
    assert len(requests) == 1


@pytest.mark.parametrize('entries', [
    [{'id': 'a9', 'view': 'front', 'framing': 'screen', 'photo': False}],
    [{'id': 'a0', 'view': 'front', 'framing': 'screen', 'photo': 'false'}],
    [{'id': 'a0', 'view': 'flagship', 'framing': 'screen', 'photo': False}],
    [{'id': 'a0', 'view': 'front', 'framing': 'screen', 'photo': False, 'model': 'guessed'}],
    [],
])
def test_asset_description_rejects_unknown_or_invalid_observations(monkeypatch, entries):
    from app.design_image_tools import describe_design_assets
    mock(monkeypatch, lambda request: httpx.Response(200, json={'choices': [{'message': {
        'content': json.dumps({'assets': entries})}}]}))
    assets = {'asset': {'data_url': 'data:image/png;base64,' + base64.b64encode(png()).decode()}}
    assert asyncio.run(describe_design_assets(assets, 'https://vision.example/v1', 'secret', 'vl')) == {}


def test_asset_description_never_fetches_urls_and_missing_credentials_skip_network(monkeypatch):
    from app.design_image_tools import describe_design_assets
    mock(monkeypatch, lambda request: pytest.fail('unnecessary network'))
    assert asyncio.run(describe_design_assets({'asset': {'url': 'https://private.example/photo.png'}},
        'https://vision.example/v1', 'secret', 'vl')) == {}
    assert asyncio.run(describe_design_assets({'asset': {'data_url': 'data:image/png;base64,AAAA'}},
        'https://vision.example/v1', '', 'vl')) == {}


def test_asset_description_network_failure_returns_no_guess(monkeypatch):
    from app.design_image_tools import describe_design_assets
    calls = []
    def handler(request):
        calls.append(request)
        return httpx.Response(429, json={'error': {'message': 'CANARY_SECRET'}})
    mock(monkeypatch, handler)
    assets = {'rear-camera-view': {'data_url': 'data:image/png;base64,' + base64.b64encode(png()).decode()}}
    result = asyncio.run(describe_design_assets(assets, 'https://vision.example/v1', 'CANARY_SECRET', 'vl'))
    assert result == {} and len(calls) == 1


def test_asset_description_limits_contact_sheet_to_eight(monkeypatch):
    from app.design_image_tools import describe_design_assets
    def handler(request):
        body = json.loads(request.content)
        assert 'a8' not in body['messages'][1]['content'][0]['text']
        result = {'assets': [{'id': f'a{index}', 'view': 'unknown', 'framing': 'abstract source', 'photo': False}
                             for index in range(8)]}
        return httpx.Response(200, json={'choices': [{'message': {'content': json.dumps(result)}}]})
    mock(monkeypatch, handler)
    assets = {f'asset-{index}': {'data_url': 'data:image/png;base64,' + base64.b64encode(png()).decode()}
              for index in range(10)}
    result = asyncio.run(describe_design_assets(assets, 'https://vision.example/v1', 'secret', 'vl'))
    assert len(result) == 8 and 'asset-8' not in result


def test_late_launch_visual_and_store_photo_are_observed_before_duplicate_angles(monkeypatch):
    from app.design_image_tools import describe_design_assets
    def handler(request):
        body = json.loads(request.content)
        encoded = body['messages'][1]['content'][1]['image_url']['url'].split(',', 1)[1]
        with Image.open(io.BytesIO(base64.b64decode(encoded))) as sheet:
            # Primary product, launch visual, then store photo in the contact sheet.
            assert sheet.getpixel((480, 150))[2] > 240
            assert sheet.getpixel((160, 430))[1] > 110
        return httpx.Response(200, json={'choices': [{'message': {'content': json.dumps({'assets': [
            {'id': f'a{i}', 'view': 'unknown', 'framing': 'visible source structure', 'photo': False}
            for i in range(8)]})}}]})
    mock(monkeypatch, handler)
    assets = {f'angle-{i}': {'data_url': 'data:image/png;base64,' + base64.b64encode(png('red')).decode()}
              for i in range(10)}
    assets['launch'] = {'visual_role': 'campaign_key_visual',
        'data_url': 'data:image/png;base64,' + base64.b64encode(png('blue')).decode()}
    assets['store'] = {'visual_role': 'store_photo',
        'data_url': 'data:image/png;base64,' + base64.b64encode(png('green')).decode()}
    result = asyncio.run(describe_design_assets(assets, 'https://vision.example/v1', 'secret', 'vl'))
    assert len(result) == 8 and 'launch' in result and 'store' in result
    assert 'angle-8' not in result
