"""Offline provenance and deterministic composition checks, not taste scores."""
import asyncio
import base64
from copy import deepcopy
import hashlib
import io

from PIL import Image
import pytest

from app.design.renderer import render_design_svg
from app.design.source_integrity import native_source_proof
from app.poster import PosterBrief, PosterCanvas, PosterDocument, PosterLayer
from app.poster_render import render_png
from test_design_context_integration import pipeline, execute
from test_generation_jobs_durability import identities


@pytest.fixture
def source_document():
    image = Image.new('RGB', (80, 40), '#15a377')
    for x in range(40, 80):
        for y in range(40):
            image.putpixel((x, y), (25, 60, 180))
    output = io.BytesIO()
    image.save(output, 'PNG')
    raw = output.getvalue()
    asset = {'source_id': 'authorized-source', 'sha256': hashlib.sha256(raw).hexdigest(),
             'data_url': 'data:image/png;base64,' + base64.b64encode(raw).decode()}
    document = PosterDocument(width=320, height=320, canvas=PosterCanvas(width=320, height=320),
        brief=PosterBrief(width=320, height=320), assets={'source': deepcopy(asset)},
        layers=[PosterLayer(id='real', type='image', role='product', locked=True,
            asset_id='source', x=40, y=40, width=160, height=160)])
    return document, {'source': asset}, raw


@pytest.mark.parametrize('role,fit', [('product', 'contain'), ('photograph', 'slice')])
def test_real_source_bytes_and_hash_survive_programmatic_placement(source_document, role, fit):
    document, authorized, raw = source_document
    document.layers[0].role = role
    proof = native_source_proof(document, authorized)
    assert proof['render_policy'] == 'asset_native.v3'
    assert proof['source_bytes_unchanged'] is True
    row = proof['placements'][0]
    assert row['source_sha256'] == hashlib.sha256(raw).hexdigest()
    assert row['source_id'] == 'authorized-source'
    assert row['fit'] == fit
    assert row['display_box'] == {'x': 40, 'y': 40, 'width': 160, 'height': 160}
    svg = render_design_svg(document)
    assert authorized['source']['data_url'] in svg
    assert ('xMidYMid meet' if role == 'product' else 'xMidYMid slice') in svg
    assert base64.b64decode(document.assets['source']['data_url'].split(',', 1)[1]) == raw


def test_generated_design_plate_is_separate_from_real_product_proof(source_document):
    document, authorized, _ = source_document
    plate = {**deepcopy(authorized['source']), 'source_id': '',
        'provenance': 'ai_generated_design_elements', 'model': 'test-image-model',
        'request_id': 'request-1'}
    authorized['design-plate'] = plate
    document.assets['design-plate'] = deepcopy(plate)
    document.layers.append(PosterLayer(id='abstract', type='image', role='design_element',
        locked=True, asset_id='design-plate', x=0, y=0, width=320, height=320, z=-20))
    proof = native_source_proof(document, authorized)
    assert len(proof['placements']) == 1
    assert proof['placements'][0]['asset_id'] == 'source'
    assert proof['design_elements'][0]['request_id'] == 'request-1'
    assert 'not_product_or_scene_evidence' in proof['design_elements'][0]['purpose']
    document.assets['design-plate']['provenance'] = 'official'
    with pytest.raises(ValueError, match='unbound_design_element'):
        native_source_proof(document, authorized)


@pytest.mark.parametrize('mutation,code', [
    ('unknown', 'native_design_unknown_asset'),
    ('bytes', 'native_design_source_bytes_changed'),
    ('hash', 'native_design_source_hash_mismatch'),
    ('source_id', 'native_design_source_hash_mismatch'),
    ('unlocked', 'native_design_requires_authorized_image_layers'),
    ('generated', 'native_design_requires_authorized_image_layers'),
])
def test_untrusted_or_regenerated_image_layers_are_rejected(source_document, mutation, code):
    document, authorized, _ = source_document
    if mutation == 'unknown':
        document.layers[0].asset_id = 'unknown'
    elif mutation == 'bytes':
        document.assets['source']['data_url'] = 'data:image/png;base64,' + base64.b64encode(b'replacement').decode()
    elif mutation == 'hash':
        document.assets['source']['sha256'] = '0' * 64
    elif mutation == 'source_id':
        document.assets['source']['source_id'] = 'another-source'
    elif mutation == 'unlocked':
        document.layers[0].locked = False
    else:
        document.layers[0].role = 'generated_design'
    with pytest.raises(ValueError, match='^' + code + '$'):
        native_source_proof(document, authorized)


@pytest.mark.parametrize('role,fit', [('product', 'meet'), ('photograph', 'slice')])
def test_scaled_composition_region_matches_reproducible_source_render(source_document, role, fit):
    document, authorized, raw = source_document
    document.layers[0].role = role
    rendered = render_png(render_design_svg(document), 320, 320)
    # Independent source-only viewport: compare output composition to its
    # recorded fit transform, not to the unscaled original pixel array.
    reference_svg = ('<svg xmlns="http://www.w3.org/2000/svg" width="320" height="320">'
        f'<rect width="160" height="160" fill="{document.canvas.background}"/>'
        '<image x="0" y="0" width="160" height="160" '
        f'href="{authorized["source"]["data_url"]}" preserveAspectRatio="xMidYMid {fit}"/></svg>')
    reference = render_png(reference_svg, 320, 320)
    with Image.open(io.BytesIO(rendered)) as actual, Image.open(io.BytesIO(reference)) as expected:
        assert actual.convert('RGBA').crop((40, 40, 200, 200)).tobytes() == expected.convert('RGBA').crop((0, 0, 160, 160)).tobytes()
    assert hashlib.sha256(raw).hexdigest() == authorized['source']['sha256']


@pytest.mark.real_tenancy
def test_configured_image_key_default_never_calls_full_frame_or_scene_generation(pipeline, monkeypatch):
    from app import design_service
    assert design_service.settings.image_api_key

    async def forbidden(*args, **kwargs):
        pytest.fail('Native delivery must not call paid image generation treatments')

    monkeypatch.setattr(design_service, '_commercial_treatment', forbidden)
    monkeypatch.setattr(design_service, '_scene_treatment', forbidden)
    artifact, work = execute(pipeline)
    assert artifact['render_policy'] == work['render_policy'] == 'asset_native.v3'
    assert pipeline.captures == []
    assert artifact['artboards']
    for board in artifact['artboards']:
        assert board['source_integrity']['source_bytes_unchanged'] is True
        for placement in board['source_integrity']['placements']:
            assert placement['source_sha256'] == pipeline.asset['sha256']
            assert placement['role'] in {'product', 'photograph'}
    assert all(board['review']['review_completed'] and board['review']['marketing_action'] == 'deliver'
               for board in artifact['artboards'])
