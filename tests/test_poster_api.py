"""Poster HTTP contracts: provenance, locked layers, drafts and raster exports."""
import base64
import copy
import io
import json
from types import SimpleNamespace

from fastapi.responses import JSONResponse, Response
from fastapi.testclient import TestClient
from PIL import Image
import pytest

from app import api, poster_routes
from app.image_service import ImageServiceError


def raster(size=(64, 64)):
    stream = io.BytesIO()
    Image.new('RGB', size, '#dddde8').save(stream, format='PNG')
    return stream.getvalue()


@pytest.fixture
def harness(monkeypatch, tmp_path):
    poster_routes.documents.clear()
    api.runs.clear()
    monkeypatch.setattr(poster_routes, 'ROOT', tmp_path)
    monkeypatch.setattr(poster_routes.settings, 'image_api_key', 'test-not-a-real-key')
    monkeypatch.setattr(poster_routes.settings, 'image_model', 'test-image')
    source = {
        'run_id': 'source-1', 'status': 'draft', 'title': '把灵感带到课堂',
        'intro': '课前整理，课后继续。', 'cta': '预约到店体验',
        'blocks': [{'text': '轻松整理学习资料', 'fact_ids': ['f1'], 'disclosures': []}],
        'assets': [{'id': 'product-1', 'product': 'MateBook Test', 'source_id': 's1',
                    'title': '真实产品图', 'verified': True, 'provenance': 'local_official',
                    'visual_role': 'product_overview'}],
        'images': [{'asset_id': 'product-1'}], 'audit': [],
    }
    task = {'product': 'MateBook Test', 'audience': '大学生', 'scene': '图书馆学习',
            'platform': 'poster', 'mode': 'local'}
    calls = {'run': 0, 'asset': 0, 'image': 0}

    async def run_task(task, request, brief=None):
        calls['run'] += 1
        record = copy.deepcopy(source)
        api.runs[record['run_id']] = copy.deepcopy(record)
        return JSONResponse(record)

    async def asset(asset_id):
        calls['asset'] += 1
        assert asset_id == 'product-1'
        return Response(raster(), media_type='image/png')

    class Images:
        async def generate_background(self, prompt, width, height):
            calls['image'] += 1
            assert 'No laptop' in prompt
            return SimpleNamespace(data=raster(), mime='image/png', model='test-image', width=64, height=64)

        async def list_models(self):
            return [{'id': 'gpt-image-test', 'capability_verified': False}]

    monkeypatch.setattr(poster_routes, 'poster_source', run_task)
    monkeypatch.setattr(api, 'asset', asset)
    monkeypatch.setattr(poster_routes, '_image_client', lambda model=None: Images())
    return SimpleNamespace(client=TestClient(api.app, raise_server_exceptions=False), source=source,
                           task=task, calls=calls, root=tmp_path)


def create(harness, brief=None, **extra):
    return harness.client.post('/api/posters', json={'task': harness.task,
        'brief': brief or {'size_preset': 'portrait'}, **extra})


@pytest.mark.parametrize('mode', ['local', 'demo', 'live'])
def test_poster_pipeline_retains_draft_and_exact_product_bytes(harness, mode):
    harness.task['mode'] = mode
    response = create(harness)
    assert response.status_code == 200, response.text
    result = response.json()
    assert result['status'] == 'draft'
    doc = result['document']
    product = next(layer for layer in doc['layers'] if layer['role'] == 'product')
    assert product['locked'] is True
    assert doc['assets'][product['asset_id']]['data_url'] == 'data:image/png;base64,' + base64.b64encode(raster()).decode()
    assert harness.calls == {'run': 1, 'asset': 1, 'image': 0}
    assert (harness.root / 'runtime' / 'posters' / (result['id'] + '.json')).is_file()


def test_source_errors_block_before_product_or_background_requests(harness):
    harness.source['audit'] = [{'severity': 'error', 'category': 'fact', 'message': '事实不匹配'}]
    result = create(harness, {'background_mode': 'generate'}).json()
    assert result['status'] == 'blocked'
    assert 'document' not in result
    assert harness.calls['asset'] == harness.calls['image'] == 0


def test_clarification_does_not_export_design(harness):
    harness.source.update(status='needs_input', questions=['请选择准确型号'])
    result = create(harness).json()
    assert result['status'] == 'needs_input'
    assert result['questions'] == ['请选择准确型号']
    assert not poster_routes.documents


def test_cached_source_cannot_be_rebound_to_another_task(harness):
    first = create(harness).json()
    assert first['document']['source_run_id'] == 'source-1'
    harness.task['scene'] = '另一个场景'
    response = create(harness, source_run_id='source-1')
    assert response.status_code == 409
    assert harness.calls['run'] == 1


def test_cached_source_errors_cannot_be_bypassed(harness):
    create(harness)
    api.runs['source-1']['audit'] = [{'severity': 'error', 'category': 'fact', 'message': '阻断'}]
    response = create(harness, source_run_id='source-1')
    assert response.json()['status'] == 'blocked'
    assert harness.calls['asset'] == 1


def test_missing_product_yields_blocked_document_and_no_export(harness):
    harness.source['assets'] = []
    result = create(harness).json()
    assert result['status'] == 'blocked'
    assert harness.client.get(result['exports']['png']).status_code == 409


def test_invalid_product_bytes_are_not_silently_replaced(harness, monkeypatch):
    async def bad_asset(asset_id):
        return Response(b'<html>not an image</html>')
    monkeypatch.setattr(api, 'asset', bad_asset)
    assert create(harness).status_code == 422
    assert not poster_routes.documents


def test_corrupt_png_magic_is_not_accepted_as_real_product(harness, monkeypatch):
    async def bad_asset(asset_id):
        return Response(b'\x89PNG\r\n\x1a\nwrong', media_type='image/png')
    monkeypatch.setattr(api, 'asset', bad_asset)
    assert create(harness).status_code == 422
    assert not poster_routes.documents


def test_live_mode_does_not_accept_generated_background(harness):
    harness.task['mode'] = 'live'
    response = create(harness, {'background_mode': 'generate'})
    assert response.status_code == 422
    assert harness.calls['image'] == harness.calls['run'] == 0


def test_background_is_separate_from_real_product_and_stays_draft(harness):
    response = create(harness, {'background_mode': 'generate'})
    assert response.status_code == 200, response.text
    result = response.json()
    assert result['status'] == 'draft'
    doc = result['document']
    background = next(layer for layer in doc['layers'] if layer['role'] == 'generated_background')
    product = next(layer for layer in doc['layers'] if layer['role'] == 'product')
    assert background['asset_id'] != product['asset_id']
    assert doc['assets'][background['asset_id']]['provenance'] == 'ai_generated_background'
    assert doc['assets'][product['asset_id']]['provenance'] == 'local_official'
    assert harness.calls['image'] == 1


def test_generation_failure_is_safe_and_not_saved_as_success(harness, monkeypatch):
    class BadImages:
        async def generate_background(self, *args):
            raise ImageServiceError('CANARY_SECRET https://private.example/token', code=401)
    monkeypatch.setattr(poster_routes, '_image_client', lambda model=None: BadImages())
    response = create(harness, {'background_mode': 'generate'})
    assert response.status_code == 502
    assert 'CANARY_SECRET' not in response.text
    assert not poster_routes.documents


def test_edit_preserves_product_and_marks_modified_copy_as_draft(harness):
    result = create(harness).json()
    headline = next(layer for layer in result['document']['layers'] if layer['role'] == 'headline')
    response = harness.client.patch('/api/posters/' + result['id'], json={'edits': [
        {'id': headline['id'], 'changes': {'text': '<script>alert(1)</script>'}}]})
    assert response.status_code == 200, response.text
    edited = response.json()
    assert edited['status'] == 'draft'
    assert edited['document']['version'] == 2
    preview = harness.client.get(edited['preview_url'])
    assert '<script>' not in preview.text
    assert '&lt;script&gt;' in preview.text
    assert 'sandbox' in preview.headers['content-security-policy']


@pytest.mark.parametrize('role', ['product', 'watermark'])
def test_locked_layers_cannot_be_removed_or_unlocked(harness, role):
    result = create(harness).json()
    layer = next(item for item in result['document']['layers'] if item['role'] == role)
    response = harness.client.patch('/api/posters/' + result['id'], json={'edits': [
        {'id': layer['id'], 'changes': {'visible': False, 'locked': False}}]})
    assert response.status_code == 422


def test_full_document_cannot_replace_asset_source_or_status(harness):
    result = create(harness).json()
    doc = result['document']
    doc['assets']['product-1']['data_url'] = 'https://127.0.0.1/private'
    doc['status'] = 'ready'
    response = harness.client.patch('/api/posters/' + result['id'], json={'document': doc})
    assert response.status_code in (200, 422)
    if response.status_code == 200:
        saved = response.json()
        assert saved['status'] == 'draft'
        assert saved['document']['assets']['product-1']['data_url'].startswith('data:image/png;base64,')


@pytest.mark.parametrize('bad_layer', [1, {'id': []}, {'id': {}}])
def test_malformed_full_document_layers_returns_validation_error(harness, bad_layer):
    result = create(harness).json()
    doc = result['document']
    doc['layers'] = [bad_layer] * len(doc['layers'])
    response = harness.client.patch('/api/posters/' + result['id'], json={'document': doc})
    assert response.status_code == 422


def test_model_listing_resets_tested_status_when_service_changes(harness, monkeypatch):
    monkeypatch.setattr(poster_routes, '_model_cache', {
        'fingerprint': 'different-service', 'models': [], 'tested': {'gpt-image-test': True}})
    response = harness.client.get('/api/image-models')
    assert response.status_code == 200
    assert response.json()['models'] == [{'id': 'gpt-image-test', 'capability_verified': False,
                                         'generation_tested': False}]


def test_png_export_calls_renderer_with_canvas_size(harness, monkeypatch):
    from app import poster_render
    seen = []
    def render(svg, width, height):
        seen.append((svg, width, height))
        return raster((width, height))
    monkeypatch.setattr(poster_render, 'render_png', render)
    result = create(harness, {'size_preset': 'square'}).json()
    response = harness.client.get(result['exports']['png'])
    assert response.status_code == 200
    assert response.headers['content-type'] == 'image/png'
    with Image.open(io.BytesIO(response.content)) as actual:
        assert actual.size == (1080, 1080)
    assert seen[0][1:] == (1080, 1080)
    assert '设计草稿' in seen[0][0]


def test_renderer_failure_keeps_svg_json_exports_available(harness, monkeypatch):
    from app import poster_render
    def bad_render(*args):
        raise RuntimeError('CANARY_SECRET')
    monkeypatch.setattr(poster_render, 'render_png', bad_render)
    result = create(harness).json()
    png = harness.client.get(result['exports']['png'])
    assert png.status_code == 503
    assert 'CANARY_SECRET' not in png.text
    assert harness.client.get(result['exports']['svg']).status_code == 200
    assert harness.client.get(result['exports']['json']).status_code == 200


def test_poster_id_traversal_and_unsupported_export_are_rejected(harness):
    assert harness.client.get('/api/posters/not-valid/preview.svg').status_code == 404
    result = create(harness).json()
    assert harness.client.get('/api/posters/' + result['id'] + '/export?format=pdf').status_code == 422
