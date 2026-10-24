"""Final visual-review context reaches both the model and marketing validator.

The six-dimension marketing contract is tested separately. This test spies only
on its input while retaining the real HTTP client and multimodal request shape.
"""
import asyncio
import base64
import hashlib
import io
import json

import httpx
from PIL import Image
import pytest

from app import design_review
from app.poster import PosterBrief, PosterCanvas, PosterDocument, PosterLayer


def png(color='green'):
    buffer = io.BytesIO()
    Image.new('RGB', (320, 480), color).save(buffer, 'PNG')
    return buffer.getvalue()


def data_url(color='blue'):
    return 'data:image/png;base64,' + base64.b64encode(png(color)).decode()


def raster_document():
    return PosterDocument(id='generated-raster', width=320, height=480,
        canvas=PosterCanvas(width=320, height=480),
        brief=PosterBrief(size_preset='custom', width=320, height=480, purpose='真实门店任务'),
        layers=[PosterLayer(id='complete-design', type='image', role='generated_design', locked=True,
            x=0, y=0, width=320, height=480, asset_id='rendered')],
        assets={'rendered': {'data_url': data_url()}})


def response_review():
    return {'review_completed': True, 'score': 75, 'edits': [],
        'checks': [{'category': category, 'severity': 'info', 'message': '具体画面观察夹具'}
            for category in ('composition', 'typography', 'imagery', 'platform', 'specificity')],
        'marketing_review': {'fixture': 'marketing-validator-input'}}


@pytest.fixture
def captured_review(monkeypatch):
    requests, validations = [], []
    original_client = httpx.AsyncClient
    def handler(request):
        requests.append(json.loads(request.content))
        return httpx.Response(200, json={'choices': [{'message': {
            'content': json.dumps(response_review(), ensure_ascii=False)}}]})
    transport = httpx.MockTransport(handler)
    def client(*args, **kwargs):
        kwargs['transport'] = transport
        return original_client(*args, **kwargs)
    def marketing_spy(raw, actual_png, *, public_text='', public_surfaces=None):
        validations.append({'raw': raw, 'png': actual_png, 'public_text': public_text,
                            'public_surfaces': public_surfaces})
        return {'review_completed': True, 'action': 'deliver',
                'render_sha256': hashlib.sha256(actual_png).hexdigest()}
    monkeypatch.setattr(httpx, 'AsyncClient', client)
    monkeypatch.setattr(design_review, 'validate_marketing_visual_review', marketing_spy)
    return requests, validations


def call(document, context):
    rendered = png('red')
    result = asyncio.run(design_review.review_with_model(rendered, document,
        'https://review.example/v1', 'fixture-not-real-secret', 'fixture-review', design_context=context))
    return rendered, result


def request_parts(request):
    parts = request['messages'][1]['content']
    metadata = json.loads(parts[0]['text'])
    images = [part['image_url']['url'] for part in parts if part['type'] == 'image_url']
    return metadata, images


def test_raster_drawn_copy_is_in_public_text_for_independent_final_marketing_gate(captured_review):
    requests, validations = captured_review
    document = raster_document()
    assert not any(layer.type == 'text' for layer in document.layers)
    rendered, result = call(document, {
        'marketing_context': {'published_copy': {'title': '这次文章的原有标题', 'intro': '原有正文'}},
        'image_copy': ['新画面已经获准的短标题', 'HUAWEI Mate 90', None, 18],
        'source_reference_images': [data_url()], 'platform': 'xiaohongshu'})
    assert result['review_completed'] is True
    assert result['marketing_review']['review_completed'] is True
    assert len(requests) == len(validations) == 1
    metadata, images = request_parts(requests[0])
    public_text = metadata['public_text']
    assert '这次文章的原有标题' in public_text
    assert '原有正文' in public_text
    assert '新画面已经获准的短标题' in public_text
    assert 'HUAWEI Mate 90' in public_text
    assert 'None' not in public_text and '18' not in public_text
    assert validations[0]['public_text'] == public_text
    assert validations[0]['png'] == rendered
    assert validations[0]['raw'] == {'fixture': 'marketing-validator-input'}
    assert images[0] == 'data:image/png;base64,' + base64.b64encode(rendered).decode()
    assert images[1] == data_url()
    assert 'source_reference_images' not in metadata['context']
    assert data_url() not in requests[0]['messages'][1]['content'][0]['text']
    assert metadata['context']['platform'] == 'xiaohongshu'
    assert metadata['layers'][0]['type'] == 'image'
    assert requests[0]['max_tokens'] == 5200


def test_source_pixels_follow_actual_render_and_are_bounded_to_three_references(captured_review):
    requests, _ = captured_review
    refs = [data_url(color) for color in ('blue', 'green', 'yellow', 'purple')]
    rendered, result = call(raster_document(), {
        'marketing_context': {'published_copy': {'title': '已有标题'}},
        'source_reference_images': refs, 'image_copy': ['画面文案']})
    assert result['review_completed'] is True
    metadata, images = request_parts(requests[0])
    assert len(images) == 4
    assert images[0] == 'data:image/png;base64,' + base64.b64encode(rendered).decode()
    assert images[1:] == refs[:3]
    assert refs[3] not in json.dumps(requests[0])
    assert 'source_reference_images' not in metadata['context']
    assert all(reference not in requests[0]['messages'][1]['content'][0]['text'] for reference in refs)


@pytest.mark.parametrize('invalid_references', [
    ['https://private.example/image.png', 'http://127.0.0.1/internal', 'file:///private/image.png'],
    ['data:image/svg+xml;base64,PHN2Zy8+', 'data:text/html;base64,PGgxPg==', None],
])
def test_external_and_unsupported_references_do_not_enter_vision_parts(captured_review, invalid_references):
    requests, validations = captured_review
    rendered, result = call(raster_document(), {
        'marketing_context': {'published_copy': {'title': '已有标题'}},
        'image_copy': ['画面标题'], 'source_reference_images': invalid_references})
    assert result['review_completed'] is True and len(validations) == 1
    metadata, images = request_parts(requests[0])
    assert images == ['data:image/png;base64,' + base64.b64encode(rendered).decode()]
    assert 'source_reference_images' not in metadata['context']
    serialized = json.dumps(requests[0], ensure_ascii=False)
    assert all(value not in serialized for value in invalid_references if isinstance(value, str))


def test_native_visible_text_and_drawn_copy_both_reach_gate_hidden_text_does_not(captured_review):
    requests, validations = captured_review
    document = raster_document()
    document.layers.extend([
        PosterLayer(id='visible', type='text', x=20, y=20, width=280, height=60, text='可见的原生文字'),
        PosterLayer(id='hidden', type='text', x=20, y=90, width=280, height=60,
                    text='隐藏的编辑草稿', visible=False)])
    _, result = call(document, {'marketing_context': {'published_copy': {'title': '已有标题'}},
                                'image_copy': ['已审定的图内新标题']})
    assert result['review_completed'] is True
    metadata, _ = request_parts(requests[0])
    assert '可见的原生文字' in metadata['public_text']
    assert '已审定的图内新标题' in metadata['public_text']
    assert '隐藏的编辑草稿' not in metadata['public_text']
    assert validations[0]['public_text'] == metadata['public_text']
