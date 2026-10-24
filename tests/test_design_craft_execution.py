"""Synthetic integration of the real bounded craft-edit service branch.

All model/network decisions and final marketing/pixel verdicts are explicit
mocks. Native planning contracts, rendering, source integrity, tenancy and
scope checks remain real. Passing proves wiring, never visual quality.
"""
import asyncio
import base64
from copy import deepcopy
import hashlib
import json
import io
from types import SimpleNamespace
from uuid import uuid4

import pytest
from fastapi import HTTPException
from pydantic import ValidationError
from PIL import Image, ImageDraw

from app import design_service as service
from app.tenancy import load_body, save_body
from test_design_context_integration import pipeline
from test_generation_jobs_durability import identities, acting

pytestmark = pytest.mark.real_tenancy


def edit(**changes):
    return {'page_id': 'cover', 'region_id': 'headline-region',
            'values': {'letter_spacing': -1.25}, 'goal': '合成测试：仅收紧字距', **changes}


@pytest.fixture
def craft_pipeline(pipeline, monkeypatch):
    from app import design, design_review, creative_agent_routes, image_service
    from app.design import vmall_materials, product_subjects, llm as design_llm
    p = pipeline
    events = {'armed': False, 'final_reviews': [], 'planner_calls': 0}
    original_json = p.model._json

    async def scripted(system, payload, **kwargs):
        if events['armed']:
            pytest.fail('An existing reviewed-copy control edit must not call a writer/artist model')
        result = await original_json(system, payload, **kwargs)
        if 'brief' in payload:
            events['planner_calls'] += 1
            for concept in result['concepts']:
                concept['production_method'] = 'native_assembly'
                for page in concept['pages']:
                    for index, region in enumerate(page['composition']):
                        region['id'] = 'headline-region' if region['role'] == 'headline' else 'image-region'
        return result
    p.model._json = scripted

    async def no_extra_sources(*args, **kwargs):
        return {}, []
    monkeypatch.setattr(vmall_materials, 'collect_vmall_design_materials', no_extra_sources)
    monkeypatch.setattr(product_subjects, 'collect_product_subjects', no_extra_sources)

    class ForbiddenExternalClient:
        def __init__(self, *args, **kwargs):
            pytest.fail('Synthetic native execution must not create a paid image/art-direction client')
    monkeypatch.setattr(image_service, 'ImageService', ForbiddenExternalClient)
    monkeypatch.setattr(design_llm, 'ArtDirectionLLM', ForbiddenExternalClient)
    monkeypatch.setattr(service.settings, 'design_text_model', '')

    async def refine(document, renderer, **kwargs):
        # Actual native bytes and deterministic geometry; marketing/pixel
        # acceptance is a synthetic fixture verdict, not a real model claim.
        raw = await renderer(document)
        checks = design_review.audit_design_document(document)
        errors = sum(row.get('severity') == 'error' for row in checks)
        review = {'errors': errors, 'warnings': 0, 'deterministic_completed': True,
            'review_completed': True, 'score': None, 'checks': checks,
            'render_sha256': hashlib.sha256(raw).hexdigest(), 'marketing_action': 'deliver',
            'marketing_review': {'review_completed': True, 'action': 'deliver',
                'render_sha256': hashlib.sha256(raw).hexdigest(), 'feedback': [],
                'fixture_kind': 'synthetic_mock_not_visual_acceptance'}}
        if events['armed']:
            assert kwargs.get('max_rounds') == 0, 'A local control edit cannot run autonomous redesign rounds'
            assert getattr(kwargs.get('reviewer'), '__name__', '') != 'compare_reviewer'
            events['final_reviews'].append(deepcopy(review))
        return {'document': document, 'review': review, 'reviews': [], 'png': raw}
    monkeypatch.setattr(design_review, 'refine_design', refine)
    with acting(p.owner):
        synthetic = load_body('run', p.run_id)
        synthetic['fixture_kind'] = 'synthetic_source_and_pixels_not_actual_product_evidence'
        save_body('run', p.run_id, synthetic)
        parent_id = uuid4().hex
        response = asyncio.run(service.execute_design({'design_id': parent_id, 'source_run_id': p.run_id,
            'platform': 'moments', 'instruction': '合成流程样例，保留原文'}))
        parent = load_body('design', json.loads(response.body)['id'])
        parent_work = load_body('design_work', parent_id)
    assert parent['status'] == 'ready' and parent_work['design_copy']['status'] == 'approved'
    assert events['planner_calls'] == 1
    events['armed'] = True

    async def unexpected_planner(*args, **kwargs):
        pytest.fail('Control edits cannot call planner or candidate comparison')
    monkeypatch.setattr(design, 'plan_design', unexpected_planner)
    monkeypatch.setattr(design_review, 'compare_candidates', lambda *a, **kw: pytest.fail('No candidate comparison'))
    def forbidden_model(**kwargs):
        pytest.fail('Control edit cannot construct an artist/writer client')
    monkeypatch.setattr(creative_agent_routes, 'model', forbidden_model)
    async def forbidden_recollection(*args, **kwargs):
        pytest.fail('A local craft edit must reuse parent-bound assets/context, not collect or research anew')
    from app import design_brand_materials, design_image_tools, store_task_research
    monkeypatch.setattr(service, 'collect_assets', forbidden_recollection)
    monkeypatch.setattr(vmall_materials, 'collect_vmall_design_materials', forbidden_recollection)
    monkeypatch.setattr(product_subjects, 'collect_product_subjects', forbidden_recollection)
    monkeypatch.setattr(design_brand_materials, 'collect_brand_materials', forbidden_recollection)
    monkeypatch.setattr(design_image_tools, 'describe_design_assets', forbidden_recollection)
    monkeypatch.setattr(store_task_research, 'prepare_store_task', forbidden_recollection)
    return SimpleNamespace(p=p, parent_id=parent_id, parent=parent, parent_work=parent_work,
                           events=events, refine=refine)


def execute(h, request=None, **changes):
    parameters = {'design_id': uuid4().hex, 'source_run_id': h.p.run_id,
        'platform': 'moments', 'surface': h.parent.get('surface', ''),
        'parent_id': h.parent_id, 'instruction': '只微调当前区域，不改文字和素材',
        'craft_edit': request or edit(), **changes}
    with acting(h.p.owner):
        response = asyncio.run(service.execute_design(parameters))
        return load_body('design', json.loads(response.body)['id']), load_body('design_work', parameters['design_id'])


def test_enqueue_requires_parent_and_rejects_another_source(craft_pipeline):
    h = craft_pipeline
    with acting(h.p.owner):
        with pytest.raises(HTTPException) as error:
            service.enqueue_design(h.p.run_id, craft_edit=edit())
        assert error.value.status_code == 422
        another = uuid4().hex
        source = load_body('run', h.p.run_id)
        save_body('run', another, {**source, 'run_id': another})
        with pytest.raises(HTTPException) as error:
            service.enqueue_design(another, parent_id=h.parent_id, craft_edit=edit())
        assert error.value.status_code == 409
        queued = service.enqueue_design(h.p.run_id, parent_id=h.parent_id, craft_edit=edit())
        job = load_body('generation_job', queued['id'])
        assert job['parameters']['parent_id'] == h.parent_id
        assert job['parameters']['craft_edit'] == edit()


def test_real_execution_reuses_reviewed_copy_and_changes_only_target_control(craft_pipeline):
    h = craft_pipeline
    artifact, work = execute(h)
    assert artifact['status'] == 'ready' and artifact['version'] == h.parent['version'] + 1
    assert artifact['parent_id'] == h.parent_id and artifact['source_run_id'] == h.p.run_id
    assert work['design_copy'] == h.parent_work['design_copy']
    expected_plan = deepcopy(h.parent['plan'])
    expected_plan['concepts'][0]['pages'][0]['composition'][0]['letter_spacing'] = -1.25
    assert artifact['plan'] == expected_plan
    assert artifact['plan']['brief']['texts'] == h.parent['plan']['brief']['texts']
    assert artifact['plan']['brief']['asset_ids'] == h.parent['plan']['brief']['asset_ids']
    assert work['craft_revision_order']['summary'] == artifact['craft_revision']
    assert artifact['craft_revision']['region_id'] == 'headline-region'
    assert artifact['craft_revision']['controls'] == ['letter_spacing']
    assert 'base_plan' not in artifact['craft_revision']
    assert work['craft_revision_order']['base_plan'] == h.parent['plan']
    assert work['concept_selection']['ranking'] == []
    assert len(h.events['final_reviews']) == len(artifact['artboards']) == 1
    board = artifact['artboards'][0]
    headline = next(layer for layer in board['document']['layers'] if layer.get('role') == 'headline')
    assert headline['letter_spacing'] == -1.25
    image_fields = ('asset_id', 'x', 'y', 'width', 'height', 'rotation')
    parent_images = [{key: layer[key] for key in image_fields} for layer in h.parent['artboards'][0]['document']['layers'] if layer['type'] == 'image']
    images = [{key: layer[key] for key in image_fields} for layer in board['document']['layers'] if layer['type'] == 'image']
    assert images == parent_images
    assert board['source_integrity']['source_bytes_unchanged'] is True
    assert board['review']['score'] is None
    with acting(h.p.owner):
        assert load_body('design', h.parent_id) == h.parent


@pytest.mark.parametrize('edit_request', [
    edit(values={'asset_id': 'different-product'}),
    edit(values={'font_scale': 4.0}),
    edit(region_id='unknown-region'),
    edit(page_id='unknown-page'),
])
def test_forbidden_or_out_of_bounds_controls_never_produce_new_artifact(craft_pipeline, edit_request):
    h = craft_pipeline
    with pytest.raises((HTTPException, ValidationError)):
        execute(h, edit_request)
    assert h.events['final_reviews'] == []
    with acting(h.p.owner):
        assert load_body('design', h.parent_id) == h.parent


def test_execute_rejects_cross_source_and_surface_change(craft_pipeline):
    h = craft_pipeline
    with acting(h.p.owner):
        another = uuid4().hex
        source = load_body('run', h.p.run_id)
        save_body('run', another, {**source, 'run_id': another})
    with pytest.raises(HTTPException) as error:
        execute(h, source_run_id=another)
    assert error.value.detail['code'] == 'craft_edit_requires_parent'
    with pytest.raises(HTTPException) as error:
        execute(h, platform='xiaohongshu')
    assert error.value.detail['code'] == 'craft_edit_cannot_change_surface'
    assert h.events['final_reviews'] == []


def test_final_marketing_revision_cannot_trigger_full_redesign(craft_pipeline, monkeypatch):
    from app import design_review
    h = craft_pipeline
    async def rejected(document, renderer, **kwargs):
        result = await h.refine(document, renderer, **kwargs)
        result['review']['marketing_action'] = 'revise'
        result['review']['marketing_review'].update(action='revise', feedback=['合成审校要求修改'])
        return result
    monkeypatch.setattr(design_review, 'refine_design', rejected)
    with pytest.raises(HTTPException) as error:
        execute(h)
    assert error.value.detail['code'] == 'marketing_revise'
    assert len(h.events['final_reviews']) == 1
    with acting(h.p.owner):
        assert load_body('design', h.parent_id) == h.parent


def test_edit_on_a_previously_locked_cover_changes_actual_render_document(craft_pipeline):
    h = craft_pipeline
    parent = deepcopy(h.parent)
    parent['plan']['provenance']['revision_constraints'] = {'locked_fields': ['composition']}
    with acting(h.p.owner):
        save_body('design', h.parent_id, parent)
    artifact, _ = execute(h)
    headline = next(layer for layer in artifact['artboards'][0]['document']['layers']
                    if layer.get('role') == 'headline')
    assert headline['letter_spacing'] == -1.25


def test_missing_parent_reviewed_copy_work_fails_409_without_writer_fallback(craft_pipeline, monkeypatch):
    h = craft_pipeline
    original_load = service.load_body
    def absent_parent_work(kind, identifier, *args, **kwargs):
        if kind == 'design_work' and identifier == h.parent_id:
            raise HTTPException(404, 'Synthetic parent work not present')
        return original_load(kind, identifier, *args, **kwargs)
    monkeypatch.setattr(service, 'load_body', absent_parent_work)
    with pytest.raises(HTTPException) as error:
        execute(h)
    assert error.value.status_code == 409
    assert error.value.detail['code'] == 'craft_edit_parent_copy_unavailable'
    assert h.events['final_reviews'] == []
    # The armed scripted model and creative model constructor already fail if
    # any writer fallback is attempted; no fake completed copy is substituted.
    assert h.events['planner_calls'] == 1


def test_generated_editorial_plate_is_preserved_during_font_edit(craft_pipeline, monkeypatch):
    from app.design.elements import attach_design_elements, PROVENANCE
    from app.poster import PosterDocument
    h = craft_pipeline
    parent = deepcopy(h.parent)
    parent['plan']['concepts'][0]['production_method'] = 'generated_editorial'
    before_doc = PosterDocument.model_validate(parent['artboards'][0]['document'])
    # Real synthetic PNG, without a generator/API. Its actual decoded bytes
    # and provenance are bound by the production attachment/proof functions.
    stream = io.BytesIO()
    Image.new('RGBA', (before_doc.width, before_doc.height), (60, 85, 100, 25)).save(stream, format='PNG')
    raw = stream.getvalue(); digest = hashlib.sha256(raw).hexdigest()
    metadata = {'id': 'design-elements-'+digest[:20], 'sha256': digest,
        'data_url': 'data:image/png;base64,'+base64.b64encode(raw).decode(),
        'mime': 'image/png', 'width': before_doc.width, 'height': before_doc.height,
        'provenance': PROVENANCE, 'visual_role': 'design_element',
        'model': 'synthetic_png_fixture_not_generated', 'request_id': 'fixture-existing-plate',
        'original_generation_sha256': digest, 'pixel_review_required': True,
        'claim_scope': 'Synthetic existing abstract plate; no product, scene or image quality claim.'}
    attached = attach_design_elements(before_doc, metadata)
    parent['artboards'][0]['document'] = attached.model_dump()
    plate_before = next(layer.model_dump() for layer in attached.layers if layer.role == 'design_element')
    metadata_before = deepcopy(attached.assets[metadata['id']])
    work = deepcopy(h.parent_work)
    work['plan'] = deepcopy(parent['plan'])
    with acting(h.p.owner):
        save_body('design', h.parent_id, parent)
        save_body('design_work', h.parent_id, work)
    audit_calls = []
    async def retained_element_audit(identifier, state, document):
        doc = document if isinstance(document, PosterDocument) else PosterDocument.model_validate(document)
        plates = [layer for layer in doc.layers if layer.role == 'design_element']
        assert len(plates) == 1 and plates[0].asset_id == metadata['id']
        assert doc.assets[metadata['id']]['sha256'] == digest
        audit_calls.append(digest)
        return {'review_completed': True, 'fixture_kind': 'synthetic_mock_not_visual_acceptance'}
    async def forbidden_regeneration(*args, **kwargs):
        pytest.fail('A font edit must restore its existing generated plate, never generate or re-art-direct it')
    monkeypatch.setattr(service, '_audit_embedded_elements', retained_element_audit)
    monkeypatch.setattr(service, '_design_elements_treatment', forbidden_regeneration)
    artifact, _ = execute(h, edit(values={'font_weight': 500}))
    board = artifact['artboards'][0]
    plate_after = next(layer for layer in board['document']['layers'] if layer.get('role') == 'design_element')
    assert plate_after == plate_before
    assert board['document']['assets'][metadata['id']] == metadata_before
    assert base64.b64decode(board['document']['assets'][metadata['id']]['data_url'].split(',', 1)[1]) == raw
    headline = next(layer for layer in board['document']['layers'] if layer.get('role') == 'headline')
    assert headline['font_weight'] == 500
    assert len(h.events['final_reviews']) == 1
    assert audit_calls
    assert board['source_integrity']['source_rgb_preserved'] is True
    assert board['source_integrity']['design_elements'][0]['sha256'] == digest
    assert board['source_integrity']['design_elements'][0]['model'] == 'synthetic_png_fixture_not_generated'


def test_font_edit_preserves_other_layers_from_actual_parent_not_stale_plan(craft_pipeline):
    h = craft_pipeline
    parent = deepcopy(h.parent)
    image = next(layer for layer in parent['artboards'][0]['document']['layers'] if layer['type'] == 'image')
    # Simulate a previous review's native control refinement recorded in the
    # actual delivered document, without pretending the original plan changed.
    image.update(x=image['x']+9, y=image['y']+6, rotation=1.25,
                 shadow_dx=4, shadow_dy=3, shadow_opacity=.12, shadow_blur=5)
    actual_before = deepcopy(image)
    stale_plan = deepcopy(parent['plan'])
    with acting(h.p.owner):
        save_body('design', h.parent_id, parent)
    artifact, _ = execute(h, edit(values={'font_weight': 500}))
    board = artifact['artboards'][0]
    after = next(layer for layer in board['document']['layers'] if layer['type'] == 'image')
    # Rendering IDs may follow the new artboard. All visible source-layer
    # content and control values must follow the latest parent, not recomposition.
    assert {key: value for key, value in after.items() if key != 'id'} == {
        key: value for key, value in actual_before.items() if key != 'id'}
    assert parent['plan'] == stale_plan
    assert next(layer for layer in board['document']['layers'] if layer.get('role') == 'headline')['font_weight'] == 500
    assert len(h.events['final_reviews']) == 1
    assert board['source_integrity']['source_bytes_unchanged'] is True


def test_two_successive_font_edits_keep_stable_bindings_and_actual_other_layers(craft_pipeline):
    h = craft_pipeline
    parent = deepcopy(h.parent)
    image = next(layer for layer in parent['artboards'][0]['document']['layers'] if layer['type'] == 'image')
    image.update(x=image['x']+7, y=image['y']+4, rotation=.75, shadow_dx=2, shadow_opacity=.1)
    with acting(h.p.owner):
        save_body('design', h.parent_id, parent)
    first, _ = execute(h, edit(values={'font_weight': 500}))
    second, second_work = execute(h, edit(values={'letter_spacing': -.75}), parent_id=first['id'])
    assert second['parent_id'] == first['id']
    assert second_work['craft_revision_order']['base_plan'] == first['plan']
    assert second['version'] == first['version']+1
    first_doc = first['artboards'][0]['document']
    second_doc = second['artboards'][0]['document']
    first_bindings = first_doc['region_bindings']
    second_bindings = second_doc['region_bindings']
    assert first_bindings and second_bindings
    assert second_bindings == first_bindings
    assert set(second_bindings.values()) <= {layer['id'] for layer in second_doc['layers']}
    target_id = second_bindings['headline-region']
    target = next(layer for layer in second_doc['layers'] if layer['id'] == target_id)
    assert target['font_weight'] == 500 and target['letter_spacing'] == -.75
    first_others = [layer for layer in first_doc['layers'] if layer['id'] != target_id]
    second_others = [layer for layer in second_doc['layers'] if layer['id'] != target_id]
    assert second_others == first_others
    assert next(layer for layer in second_doc['layers'] if layer['type'] == 'image') == image
    assert len(h.events['final_reviews']) == 2
    assert second['artboards'][0]['source_integrity']['source_bytes_unchanged'] is True
    with acting(h.p.owner):
        assert load_body('design', first['id']) == first


def test_real_refine_max_zero_reviews_but_never_applies_unrelated_suggested_edit():
    from app.design_review import refine_design
    from app.poster import PosterBrief, PosterCanvas, PosterDocument, PosterLayer
    doc = PosterDocument(width=320, height=480, canvas=PosterCanvas(width=320, height=480),
        brief=PosterBrief(size_preset='custom', width=320, height=480),
        layers=[PosterLayer(id='unrelated-shape', type='shape', x=20, y=150, width=180, height=120, fill='#335577'),
                PosterLayer(id='edited-title', type='text', x=20, y=20, width=260, height=80, text='Synthetic fixture', font_size=24)])
    original = doc.model_dump()
    canvas = Image.new('RGB', (320, 480), '#ffffff')
    ImageDraw.Draw(canvas).rectangle((20, 150, 200, 270), fill='#335577')
    stream = io.BytesIO();canvas.save(stream, 'PNG');png = stream.getvalue()
    rendered, reviewed = [], []
    async def render(actual):
        rendered.append(actual.model_dump())
        return png
    async def final_review(actual_png, actual_doc):
        assert actual_png == png
        reviewed.append(actual_doc.model_dump())
        return {'review_completed': True, 'score': None,
            'checks': [{'category': name, 'severity': 'info', 'message': 'Synthetic reviewer fixture, not visual quality evidence'}
                       for name in ['composition', 'typography', 'imagery', 'platform', 'specificity']],
            'edits': [{'id': 'unrelated-shape', 'changes': {'x': 45}}],
            'marketing_review': {'review_completed': True, 'action': 'deliver', 'feedback': [],
                                 'render_sha256': hashlib.sha256(actual_png).hexdigest()}}
    result = asyncio.run(refine_design(doc, render, reviewer=final_review, max_rounds=0))
    assert result['review']['review_completed'] is True
    assert result['review']['edits'], 'The unrelated edit must be a valid retained suggestion, not rejected input'
    assert result['review']['marketing_action'] == 'deliver'
    assert result['iterations'] == result['accepted_rounds'] == 0
    assert len(rendered) == len(reviewed) == len(result['reviews']) == 1
    assert result['document'].model_dump() == original
    assert result['png'] == png and doc.model_dump() == original
