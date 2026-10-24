"""Prepared original-alpha assets retain honest catalogue/derived provenance."""
import base64
from copy import deepcopy
import hashlib
import io

from PIL import Image, ImageDraw
import pytest

from app.design.asset_preparation import prepare_product_asset
from app.design.source_integrity import native_source_proof
from app.poster import PosterBrief, PosterCanvas, PosterDocument, PosterLayer


@pytest.fixture
def prepared_document():
    source = Image.new('RGBA', (160, 180), (0, 0, 0, 0))
    draw = ImageDraw.Draw(source)
    draw.rounded_rectangle((40, 20, 119, 159), radius=8, fill=(80, 150, 160, 255))
    draw.ellipse((50, 30, 95, 75), fill=(15, 18, 20, 255))
    draw.text((60, 120), 'H', fill=(255, 255, 255, 255))
    out = io.BytesIO()
    source.save(out, 'PNG')
    raw = out.getvalue()
    source_sha = hashlib.sha256(raw).hexdigest()
    prepared = prepare_product_asset(raw, asset_kind='packshot', expected_sha256=source_sha)
    asset = prepared.as_asset({'source_id': 'official-mate90', 'provenance': 'huawei_official'})
    asset.update(prepared_subject=True, catalog_sha256=source_sha, source_sha256=source_sha)
    document = PosterDocument(width=600, height=800, canvas=PosterCanvas(width=600, height=800),
        brief=PosterBrief(width=600, height=800), assets={'phone': deepcopy(asset)},
        layers=[PosterLayer(id='source-subject', type='image', role='product', locked=True,
                            asset_id='phone', x=70, y=150, width=300, height=525,
                            rotation=-7, shadow_opacity=.2, shadow_blur=18,
                            shadow_dx=4, shadow_dy=12)])
    return document, {'phone': asset}, source, raw, prepared


def test_alpha_crop_proof_distinguishes_catalogue_bytes_and_rendered_bytes(prepared_document):
    document, assets, source, raw, prepared = prepared_document
    proof = native_source_proof(document, assets)
    source_sha = hashlib.sha256(raw).hexdigest()
    derived_sha = hashlib.sha256(prepared.derived_bytes).hexdigest()
    assert source_sha != derived_sha
    assert proof['render_policy'] == 'asset_native.v3'
    assert proof['source_bytes_unchanged'] is False
    assert proof['source_rgb_preserved'] is True
    placement = proof['placements'][0]
    assert placement['source_sha256'] == assets['phone']['catalog_sha256'] == source_sha
    assert placement['render_asset_sha256'] == assets['phone']['sha256'] == derived_sha
    assert placement['preparation']['crop_box'] == [40, 20, 120, 160]
    assert placement['rotation'] == -7
    assert placement['shadow']['blur'] == 18
    derived = Image.open(io.BytesIO(prepared.derived_bytes))
    assert derived.convert('RGB').tobytes() == source.crop((40, 20, 120, 160)).convert('RGB').tobytes()
    assert base64.b64decode(document.assets['phone']['data_url'].split(',', 1)[1]) == prepared.derived_bytes
    assert prepared.original_bytes == raw


@pytest.mark.parametrize('field,value', [
    ('source_sha256', '0'*64),
    ('derived_sha256', '0'*64),
    ('crop_box', [0, 0, 160, 180]),
    ('rgb_policy', 'regenerated_product'),
    ('operation', {'backend': 'undocumented_inpainting'}),
])
def test_document_cannot_forge_preparation_manifest(prepared_document, field, value):
    document, assets, *_ = prepared_document
    document.assets['phone']['preparation'][field] = value
    with pytest.raises(ValueError, match='native_design_preparation_binding_mismatch'):
        native_source_proof(document, assets)


@pytest.mark.parametrize('field', ['source_sha256', 'derived_sha256', 'rgb_policy'])
def test_invalid_bound_manifest_is_rejected_even_when_copied_into_document(prepared_document, field):
    document, assets, *_ = prepared_document
    assets['phone']['preparation'][field] = 'invalid'
    document.assets['phone'] = deepcopy(assets['phone'])
    with pytest.raises(ValueError, match='native_design_preparation_binding_mismatch'):
        native_source_proof(document, assets)


def test_replacement_pixels_cannot_reuse_valid_manifest(prepared_document):
    document, assets, *_ = prepared_document
    replacement = Image.new('RGBA', (80, 140), (200, 30, 30, 255))
    out = io.BytesIO()
    replacement.save(out, 'PNG')
    document.assets['phone']['data_url'] = 'data:image/png;base64,' + base64.b64encode(out.getvalue()).decode()
    with pytest.raises(ValueError, match='native_design_source_bytes_changed'):
        native_source_proof(document, assets)
