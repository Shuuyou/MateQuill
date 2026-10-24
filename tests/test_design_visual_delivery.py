"""Release guards for paid full-frame visual delivery, not aesthetic scores.

Use real design documents and image bytes, with provider/review/checkpoint fakes.
No tenant database or external model request is needed for these boundaries.
"""
import asyncio
import base64
from copy import deepcopy
import hashlib
import io
from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from PIL import Image

from app import design_service as service
from app.design.connectors import get_platform_connector
from app.design.models import DesignArtboard, DesignBrief, DesignConcept, DesignPlan, LayoutRegion
from app.design_art_direction import DESIGN_REVIEW_PROTOCOL_VERSION
from app.image_service import ImageArtifact, ImageServiceError
from app.poster import PosterBrief, PosterCanvas, PosterDocument, PosterLayer


def image_bytes():
    stream = io.BytesIO()
    Image.new('RGB', (32, 48), '#9EA886').save(stream, 'PNG')
    return stream.getvalue()


def good_review():
    return {'protocol_version': DESIGN_REVIEW_PROTOCOL_VERSION, 'reviewed_artifact_sha256': hashlib.sha256(image_bytes()).hexdigest(),
            'review_completed': True, 'has_errors': False, 'critical_uncertainties': [],
            'checks': [{'category': 'product_reference', 'passed': True}]}


@pytest.fixture
def harness(monkeypatch):
    from app import design_art_direction, image_service
    checkpoints, generations, reviews = [], [], []
    generation_actions, review_actions = [], []
    raw = image_bytes()
    provenance = {'model': 'image-fixture', 'request_id': 'request-fixture',
                  'fidelity_status': 'reference_guided_unverified'}
    artifact = ImageArtifact(raw, 'image/png', provenance['model'], provenance['request_id'],
                             32, 48, 320, 480)
    settings = SimpleNamespace(image_api_key='fixture-not-a-real-key',
        image_base_url='https://image.example/v1', image_model='image-fixture',
        image_review_model='review-fixture')
    monkeypatch.setattr(service, 'settings', settings)
    monkeypatch.setattr(service, '_checkpoint', lambda identifier, state:
        checkpoints.append((identifier, deepcopy(state))))
    monkeypatch.setattr(image_service, 'ImageService', lambda *args, **kwargs: object())

    async def generate(client, **kwargs):
        generations.append(deepcopy(kwargs))
        action = generation_actions.pop(0) if generation_actions else None
        if isinstance(action, BaseException):
            raise action
        return SimpleNamespace(artifact=artifact, provenance=deepcopy(provenance))

    async def review(candidate, **kwargs):
        reviews.append((candidate, deepcopy(kwargs)))
        action = review_actions.pop(0) if review_actions else good_review()
        if isinstance(action, BaseException):
            raise action
        return {'protocol_version': DESIGN_REVIEW_PROTOCOL_VERSION, 'reviewed_artifact_sha256': hashlib.sha256(candidate.data).hexdigest(),
                **deepcopy(action)}

    monkeypatch.setattr(design_art_direction, 'create_commercial_artboard', generate)
    monkeypatch.setattr(design_art_direction, 'review_commercial_artboard', review)
    document = PosterDocument(id='native-doc', width=320, height=480,
        canvas=PosterCanvas(width=320, height=480),
        brief=PosterBrief(size_preset='custom', width=320, height=480),
        layers=[PosterLayer(id='old-title', type='text', x=16, y=20,
                            width=280, height=80, text='旧排版标题'),
                PosterLayer(id='old-product', type='image', x=50, y=140,
                            width=150, height=240, asset_id='source-product')],
        assets={'source-product': {'data_url': 'source-placeholder'}})
    board = DesignArtboard(id='cover-board', role='cover', title='一组真实产品图片',
        concept_id='detail-led', document=document, source_text_ids=['title'],
        visual_brief='从真实产品细节组织画面', render_mode='visual_first')
    concept = DesignConcept(id='detail-led', name='细节观察', visual_thesis='先看一个真实细节',
        composition=[LayoutRegion(role='headline', x=.05, y=.05, width=.9, height=.2)])
    plan = DesignPlan(brief=DesignBrief(platform=get_platform_connector('xiaohongshu'),
        instruction='自然、简洁', texts={'title': {'text': '从一个新角度发现它'},
                                       'unused': {'text': '不要偷偷补进图片的正文'}}),
        concepts=[concept], selected_concept_id=concept.id)
    asset = {'data_url': 'data:image/png;base64,' + base64.b64encode(raw).decode(),
             'title': board.title, 'sha256': hashlib.sha256(raw).hexdigest(),
             'provenance': deepcopy(provenance), 'width': 32, 'height': 48}
    return SimpleNamespace(board=board, plan=plan, state={}, asset=asset, artifact=artifact,
        checkpoints=checkpoints, generations=generations, reviews=reviews,
        generation_actions=generation_actions, review_actions=review_actions)


def run(h, **kwargs):
    return asyncio.run(service._commercial_treatment(h.board, h.plan, h.state, 'design-work',
        assets={'source-product': {'data_url': h.asset['data_url']}},
        page_index=kwargs.pop('page_index', 0), task_data={'audience': '本店顾客'}, **kwargs))


def completed(h, review=None, attempt=1):
    stage = {'status': 'completed', 'attempt': attempt, 'page_index': 0, 'asset': deepcopy(h.asset)}
    if review is not None:
        stage['reference_review'] = {'protocol_version': DESIGN_REVIEW_PROTOCOL_VERSION,
            'reviewed_artifact_sha256': hashlib.sha256(h.artifact.data).hexdigest(), **review}
    h.state['commercial_visuals'] = {h.board.id: stage}
    return stage


@pytest.mark.parametrize('status,expected_code', [('started', 409), ('unavailable', 503)])
def test_unknown_paid_request_is_never_replayed(harness, status, expected_code):
    h = harness
    h.state['commercial_visuals'] = {h.board.id: {'status': status}}
    with pytest.raises(HTTPException) as error:
        run(h)
    assert error.value.status_code == expected_code
    assert not h.generations and not h.reviews and not h.checkpoints


def test_success_replaces_old_type_and_reuses_paid_result_after_recovery(harness):
    h = harness
    original = h.board.document
    result = run(h)
    assert len(h.generations) == len(h.reviews) == 1
    # The first persisted state fences the paid call before a response exists.
    assert h.checkpoints[0][1]['commercial_visuals'][h.board.id]['status'] == 'started'
    assert h.checkpoints[1][1]['commercial_visuals'][h.board.id]['status'] == 'completed'
    assert 'reference_review' not in h.checkpoints[1][1]['commercial_visuals'][h.board.id]
    assert len(result.document.layers) == 1
    layer = result.document.layers[0]
    assert (layer.type, layer.role, layer.x, layer.y, layer.width, layer.height) == (
        'image', 'generated_design', 0, 0, 320, 480)
    assert set(result.document.assets) == {'visual-' + h.board.id}
    assert all(item.type != 'text' for item in result.document.layers)
    assert original.layers[0].text == '旧排版标题'  # prior native version remains intact
    assert result.document.audit[-1]['type'] == 'reference_comparison'
    assert h.generations[0]['texts'] == {'title': {'text': '从一个新角度发现它'}}
    run(h)
    assert len(h.generations) == len(h.reviews) == 1


def test_completed_render_with_missing_review_rechecks_without_paid_generation(harness):
    h = harness
    completed(h)
    run(h)
    assert not h.generations
    assert len(h.reviews) == 1
    artifact, _ = h.reviews[0]
    assert artifact.data == h.artifact.data
    assert (artifact.width, artifact.height) == (32, 48)
    assert (artifact.requested_width, artifact.requested_height) == (320, 480)


def test_unfinished_reference_review_blocks_delivery_but_keeps_render_for_recovery(harness):
    h = harness
    completed(h)
    h.review_actions.extend([{'review_completed': False, 'reason': 'review-unavailable'}, good_review()])
    with pytest.raises(HTTPException) as error:
        run(h)
    assert error.value.status_code == 503
    assert h.board.document.layers[0].id == 'old-title'
    assert h.state['commercial_visuals'][h.board.id]['asset'] == h.asset
    result = run(h)
    assert not h.generations and len(h.reviews) == 2
    assert result.document.layers[0].role == 'generated_design'


@pytest.mark.parametrize('failure', [
    {'has_errors': True, 'critical_uncertainties': []},
    {'has_errors': False, 'critical_uncertainties': ['product_geometry_uncertain']},
])
def test_reference_failures_allow_at_most_one_corrective_paid_pass(harness, failure):
    h = harness
    review = {'review_completed': True, 'checks': [{'category': 'product_reference', 'severity': 'error',
        'message': '实际成图镜组位置与授权参考图不一致，需要保持参考结构'}], **failure}
    h.review_actions.extend([review, review])
    with pytest.raises(HTTPException) as error:
        run(h)
    assert error.value.status_code == 422
    assert len(h.generations) == len(h.reviews) == 2
    stages = [state['commercial_visuals'][h.board.id] for _, state in h.checkpoints]
    repair = next(stage for stage in stages if stage['status'] == 'repair_pending')
    assert repair['attempt'] == 1
    assert repair['previous_render_sha256'] == h.asset['sha256']
    assert repair['correction_checks'] == review['checks']
    assert h.generations[1]['brief']['correction_checks'] == review['checks']
    assert h.generations[1]['brief']['instruction'] == h.plan.brief.instruction
    assert h.state['commercial_visuals'][h.board.id]['attempt'] == 2
    assert h.board.document.layers[0].id == 'old-title'  # failed visuals never become delivery
    with pytest.raises(HTTPException) as repeated:
        run(h)
    assert repeated.value.status_code == 422
    assert len(h.generations) == len(h.reviews) == 2


def test_completed_second_attempt_with_errors_never_enters_paid_repair(harness):
    h = harness
    completed(h, {'review_completed': True, 'has_errors': True, 'checks': []}, attempt=2)
    with pytest.raises(HTTPException) as error:
        run(h)
    assert error.value.status_code == 422
    assert not h.generations and not h.reviews


def test_correction_must_pass_reference_review_before_delivery(harness):
    h = harness
    h.review_actions.extend([{'review_completed': True, 'has_errors': True,
                             'checks': [{'category': 'text', 'passed': False}]}, good_review()])
    result = run(h)
    assert len(h.generations) == len(h.reviews) == 2
    assert h.state['commercial_visuals'][h.board.id]['attempt'] == 2
    assert result.document.audit[-1]['has_errors'] is False
    assert result.document.layers[0].role == 'generated_design'


def test_provider_failure_persists_unavailable_without_retrying_paid_call(harness):
    h = harness
    h.generation_actions.append(ImageServiceError('fixture-timeout', reason='timeout'))
    for _ in range(2):
        with pytest.raises(HTTPException) as error:
            run(h)
        assert error.value.status_code == 503
    assert len(h.generations) == 1 and not h.reviews
    assert h.state['commercial_visuals'][h.board.id] == {'status': 'unavailable', 'reason': 'timeout', 'detail': 'fixture-timeout'}


def test_cancelled_paid_call_leaves_started_fence_instead_of_paid_replay(harness):
    h = harness
    h.generation_actions.append(asyncio.CancelledError())
    with pytest.raises(asyncio.CancelledError):
        run(h)
    assert h.state['commercial_visuals'][h.board.id]['status'] == 'started'
    with pytest.raises(HTTPException) as error:
        run(h)
    assert error.value.status_code == 409
    assert len(h.generations) == 1 and not h.reviews


def test_explicit_empty_text_selection_has_no_title_or_brand_fallback(harness):
    h = harness
    h.board.source_text_ids = []
    h.plan.brief.instruction = '不要文字，纯照片'
    h.plan.brief.product = '不应自动补印的品牌'
    result = run(h)
    assert h.generations[0]['texts'] == {}
    assert len(result.document.layers) == 1
    assert result.document.layers[0].type == 'image'
    assert not any(layer.text for layer in result.document.layers)

@pytest.mark.parametrize('stale_reason', ['old_protocol', 'missing_protocol', 'wrong_hash'])
def test_stale_completed_reference_review_rechecks_same_png_without_new_image(harness, stale_reason):
    h = harness
    stale = good_review()
    if stale_reason == 'old_protocol':
        stale['protocol_version'] = 1
    elif stale_reason == 'missing_protocol':
        stale.pop('protocol_version')
    else:
        stale['reviewed_artifact_sha256'] = '0' * 64
    stage = completed(h, stale)
    if stale_reason == 'missing_protocol':
        # completed() creates protocol-v2 fixtures by default; this one models
        # an actual pre-versioned persisted response.
        stage['reference_review'].pop('protocol_version')
    old_review = deepcopy(stage['reference_review'])
    original_png = h.artifact.data
    result = run(h)
    assert not h.generations
    assert len(h.reviews) == 1
    assert h.reviews[0][0].data == original_png
    assert stage['reference_review_history'] == [old_review]
    assert stage['reference_review']['protocol_version'] == DESIGN_REVIEW_PROTOCOL_VERSION
    assert stage['reference_review']['reviewed_artifact_sha256'] == hashlib.sha256(original_png).hexdigest()
    assert result.document.layers[0].role == 'generated_design'
    run(h)
    assert len(h.reviews) == 1 and not h.generations  # valid v2 review now reusable


def test_valid_reference_cache_is_bound_to_actual_bytes_not_mutable_asset_hash_metadata(harness):
    h = harness
    stage = completed(h, good_review())
    stage['asset']['sha256'] = 'untrusted-metadata-digest'
    result = run(h)
    assert not h.reviews and not h.generations
    assert result.document.layers[0].role == 'generated_design'
    assert stage['reference_review']['reviewed_artifact_sha256'] == hashlib.sha256(h.artifact.data).hexdigest()


@pytest.mark.parametrize('invalid_review', [
    {'protocol_version': 1}, {'reviewed_artifact_sha256': '0' * 64},
])
def test_fresh_completed_reference_review_cannot_deliver_with_wrong_protocol_or_hash(harness, invalid_review):
    h = harness
    completed(h)
    h.review_actions.append({**good_review(), **invalid_review})
    with pytest.raises(HTTPException) as error:
        run(h)
    assert error.value.status_code == 503
    assert len(h.reviews) == 1 and not h.generations
    assert h.board.document.layers[0].id == 'old-title'


def test_helper_protocol_repair_attempts_do_not_trigger_outer_review_loop(harness):
    h = harness
    stage = completed(h)
    h.review_actions.append({'review_completed': False, 'reason': 'protocol-incomplete',
        'review_attempts': [{'outcome': 'invalid_protocol'}, {'outcome': 'invalid_protocol'}]})
    with pytest.raises(HTTPException) as error:
        run(h)
    assert error.value.status_code == 503
    assert len(h.reviews) == 1 and not h.generations
    assert stage['review_attempts'] == 2
    assert h.board.document.layers[0].id == 'old-title'


def test_stale_review_history_keeps_latest_eight_entries(harness):
    h = harness
    stage = completed(h, {**good_review(), 'protocol_version': 1})
    stage['reference_review_history'] = [{'sequence': index} for index in range(10)]
    stale = deepcopy(stage['reference_review'])
    run(h)
    assert len(stage['reference_review_history']) == 8
    assert stage['reference_review_history'][0] == {'sequence': 3}
    assert stage['reference_review_history'][-1] == stale
    assert not h.generations and len(h.reviews) == 1
