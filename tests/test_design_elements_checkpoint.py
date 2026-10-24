"""Real treatment integration with fake provider; no paid/network effects."""
import asyncio
import base64
from copy import deepcopy
import hashlib
import io
import json
from types import SimpleNamespace

from fastapi import HTTPException
from PIL import Image, ImageDraw
import pytest

from app import design_service
from app.design.asset_preparation import prepare_product_asset
from app.design.source_integrity import native_source_proof
from app import image_service
from app.design import elements
from app import design_review
from app.poster import PosterBrief, PosterCanvas, PosterDocument, PosterLayer


def png(image):
    out = io.BytesIO()
    image.save(out, 'PNG')
    return out.getvalue()


@pytest.fixture
def treatment(monkeypatch):
    image = Image.new('RGBA', (100, 150), (0, 0, 0, 0))
    ImageDraw.Draw(image).rounded_rectangle((20, 10, 79, 139), 5, fill=(30, 110, 130, 255))
    original_raw = png(image)
    prepared = prepare_product_asset(original_raw, asset_kind='packshot')
    source_sha = hashlib.sha256(original_raw).hexdigest()
    source = prepared.as_asset({'source_id': 'actual-authorized-source'})
    source.update(prepared_subject=True, catalog_sha256=source_sha, source_sha256=source_sha)
    doc = PosterDocument(width=320, height=480, canvas=PosterCanvas(width=320, height=480),
        brief=PosterBrief(width=320, height=480), assets={'product': deepcopy(source)},
        layers=[PosterLayer(id='real-product', type='image', asset_id='product', role='product',
                            x=100, y=140, width=120, height=260, locked=True),
                PosterLayer(id='actual-title', type='text', role='headline', x=20, y=20,
                            width=280, height=50, text='真实原生标题')])
    board = SimpleNamespace(id='board-one', concept_id='concept-one', document=doc,
                            visual_brief='抽象折射光与空间留白')
    plan = {'selected_concept_id': 'concept-one', 'concepts': [{
        'id': 'concept-one', 'art_direction': {'key_visual': '弧形光的关系'},
        'background': '#112233', 'foreground': '#FFFFFF', 'accent': '#337777'}]}
    snapshots, provider_calls, constructors, direction_calls, review_calls, events = [], [], [], [], [], []
    provider = SimpleNamespace(failure=False)
    async def generate_background(prompt, w, h):
        events.append('generate')
        provider_calls.append((prompt, w, h))
        if provider.failure:
            raise image_service.ImageServiceError('timeout', reason='timeout')
        return SimpleNamespace(data=png(Image.new('RGB', (64, 96), '#113344')),
                               model='test-elements-model', request_id='actual-request-id')
    provider.generate_background = generate_background
    def client(*args, **kwargs):
        constructors.append(True)
        return provider
    monkeypatch.setattr(image_service, 'ImageService', client)
    monkeypatch.setattr(design_service.settings, 'image_api_key', 'local-test-only')
    monkeypatch.setattr(design_service.settings, 'image_model', 'test-elements-model')
    monkeypatch.setattr(design_service.settings, 'image_base_url', 'https://image.example/v1')
    monkeypatch.setattr(design_service, '_checkpoint', lambda identifier, state: snapshots.append(deepcopy(state)))
    original_plan = elements.plan_design_elements
    class BoundDirector:
        enabled = True
        async def _json(self, system, payload):
            return {'motif': 'abstract converging light paths', 'material': 'matte studio surface',
                    'light': 'one teal light from upper right',
                    'spatial_instructions': 'keep source-subject and typography reservations quiet',
                    'native_layer_ids': payload['context']['native_layout']['native_layer_ids']}
    async def plan_elements(actual_plan, actual_board, director):
        events.append('direction')
        result = await original_plan(actual_plan, actual_board, BoundDirector())
        direction_calls.append(deepcopy(result))
        return result
    reviewer = SimpleNamespace(action='accept', completed=True)
    async def inspect_elements(metadata, base_url, api_key, model):
        events.append('review')
        review_calls.append(deepcopy(metadata))
        assert metadata['element_direction_sha256'] == hashlib.sha256(
            json.dumps(direction_calls[-1], ensure_ascii=False, sort_keys=True).encode()).hexdigest()
        return {'review_completed': reviewer.completed, 'action': reviewer.action,
                'protocol_version': 1, 'review_scope': 'standalone_design_element',
                'reviewed_artifact_sha256': metadata['sha256'],
                'element_direction_sha256': metadata['element_direction_sha256']}
    monkeypatch.setattr(elements, 'plan_design_elements', plan_elements)
    monkeypatch.setattr(design_review, 'review_design_element', inspect_elements)
    return SimpleNamespace(board=board, plan=plan, assets={'product': source}, snapshots=snapshots,
                           provider_calls=provider_calls, constructors=constructors, provider=provider,
                           direction_calls=direction_calls, review_calls=review_calls,
                           reviewer=reviewer, events=events)


def execute(fixture, state, *, assets=None):
    return asyncio.run(design_service._design_elements_treatment(
        'design-id', state, fixture.plan, fixture.board, assets if assets is not None else fixture.assets))


def test_treatment_checkpoints_once_then_reuses_saved_real_metadata_without_generation(treatment):
    before = treatment.board.document.model_dump()
    state = {}
    result = execute(treatment, state)
    assert len(treatment.provider_calls) == len(treatment.constructors) == 1
    assert [snapshot['design_elements']['board-one']['status'] for snapshot in treatment.snapshots] == ['started', 'started', 'ready', 'ready']
    assert treatment.events == ['direction', 'generate', 'review']
    assert 'element_direction' not in treatment.snapshots[0]['design_elements']['board-one']
    assert treatment.snapshots[1]['design_elements']['board-one']['element_direction'] == treatment.direction_calls[0]
    assert 'pixel_review' not in treatment.snapshots[2]['design_elements']['board-one']
    assert treatment.snapshots[3]['design_elements']['board-one']['pixel_review']['action'] == 'accept'
    metadata = state['design_elements']['board-one']['asset']
    assert metadata['model'] == 'test-elements-model' and metadata['request_id'] == 'actual-request-id'
    raw = base64.b64decode(metadata['data_url'].split(',', 1)[1])
    assert hashlib.sha256(raw).hexdigest() == metadata['sha256']
    assert treatment.assets[metadata['id']] == metadata
    assert treatment.board.document.model_dump() == before
    assert [layer.model_dump() for layer in result.layers[1:]] == before['layers']
    assert result.assets['product'] == before['assets']['product']
    proof = native_source_proof(result, treatment.assets)
    assert proof['source_bytes_unchanged'] is False and proof['source_rgb_preserved'] is True
    assert len(proof['placements']) == 1 and len(proof['design_elements']) == 1
    assert proof['design_elements'][0]['sha256'] == metadata['sha256']
    # JSON roundtrip simulates a restored durable checkpoint with a fresh asset map.
    restored_state = json.loads(json.dumps(state))
    restored_assets = {'product': deepcopy(treatment.assets['product'])}
    repeated = execute(treatment, restored_state, assets=restored_assets)
    assert len(treatment.provider_calls) == len(treatment.constructors) == 1
    assert len(treatment.snapshots) == 4
    assert len(treatment.direction_calls) == len(treatment.review_calls) == 1
    assert repeated.model_dump() == result.model_dump()
    assert restored_assets[metadata['id']] == metadata
    assert native_source_proof(repeated, restored_assets)['design_elements'][0]['request_id'] == 'actual-request-id'


@pytest.mark.parametrize('status', ['started', 'incomplete'])
def test_unknown_or_failed_attempt_never_reopens_paid_request(treatment, status):
    state = {'design_elements': {'board-one': {'status': status}}}
    with pytest.raises(HTTPException) as caught:
        execute(treatment, state)
    assert caught.value.status_code == 503
    assert caught.value.detail['code'] == 'design_elements_attempt_incomplete'
    assert treatment.provider_calls == treatment.constructors == treatment.snapshots == []


def test_provider_timeout_persists_incomplete_and_cannot_repeat_effect(treatment):
    treatment.provider.failure = True
    state = {}
    for expected_code in ['design_elements_incomplete', 'design_elements_attempt_incomplete']:
        with pytest.raises(HTTPException) as caught:
            execute(treatment, state)
        assert caught.value.status_code == 503
        assert caught.value.detail['code'] == expected_code
    assert len(treatment.provider_calls) == len(treatment.constructors) == 1
    assert [snapshot['design_elements']['board-one']['status'] for snapshot in treatment.snapshots] == ['started', 'started', 'incomplete']
    assert state['design_elements']['board-one']['reason'] == 'timeout'
    assert set(treatment.assets) == {'product'}


def test_start_checkpoint_interruption_does_not_generate_before_durable_start(treatment, monkeypatch):
    def crash(identifier, state):
        raise RuntimeError('checkpoint interruption')
    monkeypatch.setattr(design_service, '_checkpoint', crash)
    state = {}
    with pytest.raises(RuntimeError, match='checkpoint interruption'):
        execute(treatment, state)
    assert state['design_elements']['board-one']['status'] == 'started'
    assert treatment.provider_calls == treatment.constructors == []
    with pytest.raises(HTTPException):
        execute(treatment, state)
    assert treatment.provider_calls == treatment.constructors == []


def test_rejected_pixels_never_attach_and_restored_rejection_never_pays_again(treatment):
    treatment.reviewer.action = 'reject'
    state = {}
    original = treatment.board.document.model_dump()
    for current in (state, None):
        if current is None:
            current = json.loads(json.dumps(state))
        with pytest.raises(HTTPException) as caught:
            execute(treatment, current)
        assert caught.value.status_code == 422
        assert caught.value.detail['code'] == 'design_elements_rejected'
    assert state['design_elements']['board-one']['status'] == 'rejected'
    assert set(treatment.assets) == {'product'}
    assert treatment.board.document.model_dump() == original
    assert len(treatment.provider_calls) == len(treatment.review_calls) == len(treatment.direction_calls) == 1


def test_incomplete_pixel_review_remains_incomplete_on_restore_without_fake_pass(treatment):
    treatment.reviewer.completed = False
    treatment.reviewer.action = 'incomplete'
    state = {}
    with pytest.raises(HTTPException) as caught:
        execute(treatment, state)
    assert caught.value.status_code == 503
    assert caught.value.detail['code'] == 'design_elements_review_incomplete'
    treatment.reviewer.completed = True
    treatment.reviewer.action = 'accept'
    restored = json.loads(json.dumps(state))
    with pytest.raises(HTTPException) as repeated:
        execute(treatment, restored)
    assert repeated.value.detail['code'] == 'design_elements_review_incomplete'
    assert restored['design_elements']['board-one']['pixel_review']['review_completed'] is False
    assert set(treatment.assets) == {'product'}
    assert len(treatment.provider_calls) == len(treatment.review_calls) == len(treatment.direction_calls) == 1


@pytest.mark.parametrize('stale', [None, {'protocol_version': 0},
    {'review_scope': 'final_composite'}, {'reviewed_artifact_sha256': 'different-pixels'}])
def test_embedded_old_board_requires_current_protocol_scope_and_pixel_binding(treatment, stale):
    state = {}
    document = execute(treatment, state)
    digest = state['design_elements']['board-one']['asset']['sha256']
    if stale is None:
        state['element_pixel_reviews'] = {}
    else:
        state['element_pixel_reviews'][digest].update(stale)
    asyncio.run(design_service._audit_embedded_elements('design-id', state, document))
    assert len(treatment.review_calls) == 2
    assert len(treatment.provider_calls) == len(treatment.direction_calls) == 1
    inspection = state['element_pixel_reviews'][digest]
    assert inspection['protocol_version'] == 1
    assert inspection['review_scope'] == 'standalone_design_element'
    assert inspection['reviewed_artifact_sha256'] == digest
    # A JSON-restored board with an exactly bound current review needs no call.
    asyncio.run(design_service._audit_embedded_elements(
        'design-id', json.loads(json.dumps(state)), document.model_dump()))
    assert len(treatment.review_calls) == 2


@pytest.mark.parametrize('completed,action', [(True, 'reject'), (False, 'incomplete')])
def test_embedded_rejected_or_incomplete_pixels_cannot_pass_from_old_ready_board(treatment, completed, action):
    state = {}
    document = execute(treatment, state)
    state['element_pixel_reviews'] = {}
    treatment.reviewer.completed = completed
    treatment.reviewer.action = action
    for restored in (state, None):
        current = state if restored is not None else json.loads(json.dumps(state))
        with pytest.raises(HTTPException) as caught:
            asyncio.run(design_service._audit_embedded_elements('design-id', current, document))
        assert caught.value.status_code == 422
        assert caught.value.detail['code'] == 'embedded_design_elements_need_revision'
    assert len(treatment.review_calls) == 2
    assert len(treatment.provider_calls) == len(treatment.direction_calls) == 1


def test_treatment_legacy_pixel_review_is_reinspected_without_regenerating_asset(treatment):
    state = {}
    execute(treatment, state)
    inspection = state['design_elements']['board-one']['pixel_review']
    inspection.pop('review_scope')
    execute(treatment, json.loads(json.dumps(state)))
    assert len(treatment.review_calls) == 2
    assert len(treatment.provider_calls) == len(treatment.direction_calls) == 1
