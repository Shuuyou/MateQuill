import asyncio
import io
import pytest
from PIL import Image, ImageDraw
from app.poster import PosterBrief, build_poster
from app.models import TaskInput, RunResult, Asset
from app.design_review import review_design, refine_design, safe_design_edits, compare_candidates


def doc():
    return build_poster(TaskInput(product='PC'), RunResult(title='带着灵感出发', intro='看看屏幕上的新想法',
        assets=[Asset(id='a', product='PC', url='https://images.example/product.png', source_id='s', verified=True)]), PosterBrief())


def render(document):
    image = Image.new('RGB', (document.canvas.width, document.canvas.height), '#e9e9df')
    ImageDraw.Draw(image).rectangle((50, 50, 150, 150), fill='black')
    stream = io.BytesIO()
    image.save(stream, 'PNG')
    return stream.getvalue()


def visual(score=70, edits=None):
    return {'review_completed': True, 'checks': [{'category': 'typography', 'severity': 'info',
        'message': '标题与产品分区清晰，标题可适度加大字距'}], 'score': score, 'edits': edits or []}


def test_deterministic_review_has_no_fake_aesthetic_pass():
    document = doc()
    review = asyncio.run(review_design(render(document), document))
    assert review['deterministic_completed']
    assert not review['review_completed']
    assert review['score'] is None
    assert review['status'] == 'render_checked'


def test_blank_pixels_and_wrong_dimensions_detected():
    image = Image.new('RGB', (64, 64), 'white')
    stream = io.BytesIO()
    image.save(stream, 'PNG')
    result = asyncio.run(review_design(stream.getvalue(), doc()))
    assert {row['category'] for row in result['checks']} >= {'render_dimensions', 'render_empty'}
    assert result['errors'] >= 2


def test_structure_reflow_preserves_text_facts_and_product():
    document = doc()
    title = next(layer for layer in document.layers if layer.role == 'headline')
    product = next(layer for layer in document.layers if layer.role == 'product')
    candidate, edits, rejected = safe_design_edits(document, [
        {'id': title.id, 'changes': {'font_family': 'serif', 'letter_spacing': 2, 'z': 15}},
        {'id': title.id, 'changes': {'text': 'different product claim'}},
        {'id': product.id, 'changes': {'x': 0}},
    ])
    assert len(edits) == 1 and rejected == 2
    assert next(layer for layer in candidate.layers if layer.id == title.id).text == title.text
    assert next(layer for layer in candidate.layers if layer.id == product.id) == product


def test_repair_rerenders_and_rereviews_before_acceptance():
    document = doc()
    title = next(layer for layer in document.layers if layer.role == 'headline')
    calls = []
    async def reviewer(image, current):
        calls.append(current.version)
        return visual(80 if current.version > document.version else 65,
            [] if current.version > document.version else [{'id': title.id, 'changes': {'font_family': 'serif'}}])
    result = asyncio.run(refine_design(document, render, reviewer=reviewer))
    assert len(calls) == 2
    assert result['accepted_rounds'] == 1
    assert result['iterations'] == 1
    assert result['review']['score'] == 80
    assert document.version == 1


def test_failed_second_review_keeps_original():
    document = doc()
    title = next(layer for layer in document.layers if layer.role == 'headline')
    async def reviewer(image, current):
        if current.version > document.version:
            raise RuntimeError('upstream unavailable')
        return visual(70, [{'id': title.id, 'changes': {'font_family': 'serif'}}])
    result = asyncio.run(refine_design(document, render, reviewer=reviewer))
    assert result['document'] == document
    assert result['accepted_rounds'] == 0
    assert len(result['reviews']) == 2
    assert not result['reviews'][-1]['review_completed']


def test_invalid_completed_review_is_not_accepted():
    document = doc()
    result = asyncio.run(review_design(render(document), document,
        vision_reviewer=lambda *args: {'review_completed': True, 'checks': []}))
    assert not result['review_completed']
    assert result['score'] is None


def test_worse_candidate_keeps_original_and_iteration_limit_enforced():
    document = doc()
    title = next(layer for layer in document.layers if layer.role == 'headline')
    result = asyncio.run(refine_design(document, render, reviewer=lambda image, current: visual(
        70 if current.version == 1 else 60, [{'id': title.id, 'changes': {'font_family': 'serif'}}])))
    assert result['document'] == document
    assert result['accepted_rounds'] == 0
    with pytest.raises(ValueError):
        asyncio.run(refine_design(document, render, max_rounds=3))


def test_candidate_comparison_uses_actual_observations():
    document = doc()
    review = asyncio.run(review_design(render(document), document, vision_reviewer=lambda *args: visual()))
    selection = compare_candidates([{'id': 'a', 'review': review}, {'id': 'b', 'review': {
        **review, 'errors': 1, 'score': 99, 'checks': [{'severity': 'error', 'message': '标题被裁切'}]}}])
    assert selection['selected_id'] == 'a'
    assert [item['id'] for item in selection['ranking']] == ['a']


def test_all_invalid_or_unfinished_candidates_have_no_selection():
    baseline = {'deterministic_completed': True, 'review_completed': True,
                'errors': 0, 'warnings': 0, 'score': 99, 'checks': []}
    candidates = [{'id': str(index), 'review': {**baseline, **override}} for index, override in enumerate([
        {'errors': 1}, {'review_completed': False}, {'deterministic_completed': False},
        {'marketing_action': 'revise'}, {'marketing_action': 'incomplete'}, {'marketing_action': 'reselect'},
    ])]
    assert compare_candidates(candidates)['selected_id'] is None
    assert compare_candidates(candidates)['ranking'] == []


def test_comparison_prefers_real_score_before_warning_count_and_preserves_unknown_score():
    baseline = {'deterministic_completed': True, 'review_completed': True,
                'errors': 0, 'warnings': 0, 'score': None, 'checks': []}
    candidates = [
        {'id': 'unscored', 'review': baseline},
        {'id': 'conservative', 'review': {**baseline, 'score': 65}},
        {'id': 'creative', 'review': {**baseline, 'score': 95, 'warnings': 2}},
        {'id': 'same_score_fewer_warnings', 'review': {**baseline, 'score': 95, 'warnings': 1}},
    ]
    result = compare_candidates(candidates)
    assert result['selected_id'] == 'same_score_fewer_warnings'
    assert [item['id'] for item in result['ranking']] == [
        'same_score_fewer_warnings', 'creative', 'conservative', 'unscored']
    assert result['ranking'][-1]['score'] is None


def test_refinement_accepts_higher_score_despite_additional_nonblocking_warning():
    document = doc()
    title = next(layer for layer in document.layers if layer.role == 'headline')
    def reviewer(image, current):
        if current.version == document.version:
            return visual(65, [{'id': title.id, 'changes': {'font_family': 'serif'}}])
        result = visual(95)
        result['checks'].append({'category': 'composition', 'severity': 'warning', 'message': '可进一步调整边距'})
        return result
    result = asyncio.run(refine_design(document, render, reviewer=reviewer))
    assert result['accepted_rounds'] == 1
    assert result['review']['score'] == 95



def test_model_review_sends_actual_render_and_requires_all_design_dimensions(monkeypatch):
    import json
    import httpx
    from app.design_review import review_with_model
    document = doc()
    actual_client = httpx.AsyncClient
    def handler(request):
        body = json.loads(request.content)
        assert body['messages'][1]['content'][1]['type'] == 'image_url'
        assert 'https://images.example/product.png' not in json.dumps(body)
        instruction = body['messages'][0]['content']
        assert '不能因透明产品正确' in instruction
        assert '完整正背双视图pair' in instruction
        assert '逐字对比' in instruction
        assert 'visual_devices和设计意图只是待验证' in instruction
        assert '不必要的风格偏好只能为warning' in instruction
        checks = [{'category': name, 'severity': 'info', 'message': '根据实际成图观察：' + name}
                  for name in ('composition', 'typography', 'imagery', 'platform', 'specificity')]
        return httpx.Response(200, json={'choices': [{'message': {'content': json.dumps({
            'review_completed': True, 'checks': checks, 'score': 78, 'edits': []})}}]})
    monkeypatch.setattr(httpx, 'AsyncClient', lambda **kw: actual_client(transport=httpx.MockTransport(handler), **kw))
    result = asyncio.run(review_with_model(render(document), document, 'https://vision.example/v1', 'CANARY', 'vl'))
    assert result['review_completed'] and len(result['checks']) == 5
    assert result['score'] == 78


def test_model_review_failure_is_not_converted_into_success(monkeypatch):
    import json
    import httpx
    from app.design_review import review_with_model
    document = doc()
    actual_client = httpx.AsyncClient
    def handler(request):
        return httpx.Response(200, json={'choices': [{'message': {'content': json.dumps({
            'review_completed': True, 'checks': [], 'score': 99, 'edits': []})}}]})
    monkeypatch.setattr(httpx, 'AsyncClient', lambda **kw: actual_client(transport=httpx.MockTransport(handler), **kw))
    result = asyncio.run(review_with_model(render(document), document, 'https://vision.example/v1', 'CANARY', 'vl'))
    assert not result['review_completed']
    assert 'CANARY' not in str(result)


def test_model_review_retries_bounded_upstream_rejection(monkeypatch):
    # A 429/5xx arrives before any verdict exists; the same review must be
    # attempted again within bounds instead of failing the whole art pass.
    import json
    import httpx
    from app.design_review import review_with_model
    document = doc()
    actual_client = httpx.AsyncClient
    calls = []
    def handler(request):
        calls.append(request)
        if len(calls) == 1:
            return httpx.Response(503, json={'error': 'busy'})
        checks = [{'category': name, 'severity': 'info', 'message': 'ok ' + name}
                  for name in ('composition', 'typography', 'imagery', 'platform', 'specificity')]
        return httpx.Response(200, json={'choices': [{'message': {'content': json.dumps({
            'review_completed': True, 'checks': checks, 'score': 70, 'edits': []})}}]})
    monkeypatch.setattr(httpx, 'AsyncClient', lambda **kw: actual_client(transport=httpx.MockTransport(handler), **kw))
    monkeypatch.setattr('app.design_review._retry_wait', lambda headers, n: 0)
    result = asyncio.run(review_with_model(render(document), document, 'https://vision.example/v1', 'CANARY', 'vl'))
    assert result['review_completed'] and len(calls) == 2


def test_model_review_gives_up_after_bounded_retries(monkeypatch):
    import json
    import httpx
    from app.design_review import review_with_model
    document = doc()
    actual_client = httpx.AsyncClient
    calls = []
    def handler(request):
        calls.append(request)
        return httpx.Response(429, json={'error': 'rate limited'})
    monkeypatch.setattr(httpx, 'AsyncClient', lambda **kw: actual_client(transport=httpx.MockTransport(handler), **kw))
    monkeypatch.setattr('app.design_review._retry_wait', lambda headers, n: 0)
    result = asyncio.run(review_with_model(render(document), document, 'https://vision.example/v1', 'CANARY', 'vl'))
    assert not result['review_completed'] and len(calls) == 3


def test_natural_photo_document_is_valid_without_product_layer():
    from app.design_review import audit_design_document
    document = doc()
    product = next(layer for layer in document.layers if layer.role == 'product')
    product.role = 'photograph'
    checks = audit_design_document(document)
    assert not [item for item in checks if item['severity'] == 'error'], checks
    review = asyncio.run(review_design(render(document), document))
    assert review['deterministic_completed']
    assert not any(item['category'] == 'product' for item in review['checks'])


def test_text_only_artboard_accepts_structural_repairs():
    from app.design_review import audit_design_document
    document = doc()
    document.layers = [layer for layer in document.layers if layer.type != 'image']
    document.assets = {}
    assert not [item for item in audit_design_document(document) if item['severity'] == 'error']
    title = next(layer for layer in document.layers if layer.role == 'headline')
    updated, accepted, rejected = safe_design_edits(document, [
        {'id': title.id, 'changes': {'color': '#123456'}}])
    assert len(accepted) == 1 and rejected == 0
    assert next(layer for layer in updated.layers if layer.id == title.id).color == '#123456'


@pytest.mark.parametrize('role', ['product', 'photograph'])
def test_source_image_layers_require_real_metadata_and_protection(role):
    from app.design_review import audit_design_document
    document = doc()
    product = next(layer for layer in document.layers if layer.role == 'product')
    product.role = role
    product.locked = False
    document.assets = {}
    checks = audit_design_document(document)
    assert {item['category'] for item in checks} >= {'asset_metadata', 'asset_protection'}


def test_native_review_uses_exact_typography_instead_of_legacy_wrapping(monkeypatch):
    from app.design_review import audit_design_document
    from app.design import typography
    document = doc()
    title = next(layer for layer in document.layers if layer.role == 'headline')
    title.font_family, title.font_weight = 'serif', 800
    calls = []
    def measured_wrap(text, width, size, tracking, family, weight):
        calls.append((text, family, weight))
        return [text]
    monkeypatch.setattr(typography, 'semantic_wrap', measured_wrap)
    monkeypatch.setattr(typography, 'measure', lambda text, size, tracking, family, weight: 10)
    checks = audit_design_document(document)
    assert (title.text, title.font_family, title.font_weight) in calls
    assert not any(item['category'] == 'overflow' for item in checks)
    monkeypatch.setattr(typography, 'measure', lambda text, size, tracking, family, weight: 100000)
    assert any(item['category'] == 'overflow' for item in audit_design_document(document))


def test_full_bleed_photo_can_have_text_overlay_but_text_collision_is_error():
    from app.design_review import audit_design_document
    document = doc()
    product = next(layer for layer in document.layers if layer.role == 'product')
    product.role = 'photograph'
    product.x = product.y = 0
    product.width, product.height = document.width, document.height
    assert not any(item['category'] == 'overlap' for item in audit_design_document(document))
    texts = [layer for layer in document.layers if layer.type == 'text']
    texts[1].x, texts[1].y = texts[0].x, texts[0].y
    assert any(item['category'] == 'overlap' for item in audit_design_document(document))
