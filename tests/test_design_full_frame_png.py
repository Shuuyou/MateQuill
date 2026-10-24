"""Finished PNG reuse is allowed only for an identity composition."""
import base64
import hashlib
import io

import pytest
from PIL import Image

from app.design.compositor import apply_full_frame_visual
from app.design.renderer import try_full_frame_png
from app.poster import PosterBrief, PosterCanvas, PosterDocument, PosterLayer


def image_bytes(*, size=(320, 480), transparent=False, format='PNG'):
    image = Image.new('RGBA' if transparent else 'RGB', size, (30, 90, 60, 120) if transparent else '#357d53')
    stream = io.BytesIO()
    image.save(stream, format=format)
    return stream.getvalue()


def document(raw=None):
    raw = raw or image_bytes()
    doc = PosterDocument(width=320, height=480, canvas=PosterCanvas(width=320, height=480),
                         brief=PosterBrief(size_preset='custom', width=320, height=480), layers=[])
    doc = apply_full_frame_visual(doc, 'finished', {'data_url': 'data:image/png;base64,' + base64.b64encode(raw).decode(),
        'sha256': hashlib.sha256(raw).hexdigest()})
    return doc, raw


def test_exact_generated_png_reuses_original_bytes_and_keeps_audit_unchanged():
    doc, raw = document()
    doc.audit = [{'type': 'reference_comparison', 'review_completed': False}]
    before = doc.model_dump()
    assert try_full_frame_png(doc) == raw
    assert try_full_frame_png(doc.model_dump()) == raw
    assert doc.model_dump() == before


def test_service_full_frame_render_never_launches_browser_and_returns_same_png(monkeypatch):
    import asyncio
    from app.design_service import render_document
    def forbidden(*args, **kwargs):
        pytest.fail('A validated identity composition must not launch Chromium')
    monkeypatch.setattr('app.poster_render.render_png', forbidden)
    doc, raw = document()
    result = asyncio.run(render_document(doc))
    assert result == raw
    assert hashlib.sha256(result).digest() == hashlib.sha256(raw).digest()


def test_animation_is_not_a_single_finished_frame():
    stream = io.BytesIO()
    frames = [Image.new('RGB', (320, 480), color) for color in ('red', 'green')]
    frames[0].save(stream, format='PNG', save_all=True, append_images=frames[1:], duration=100, loop=0)
    doc, _ = document(stream.getvalue())
    assert try_full_frame_png(doc) is None


@pytest.mark.parametrize('changes', [
    {'visible': False}, {'locked': False}, {'role': 'photograph'}, {'type': 'shape'},
    {'x': 1}, {'y': 1}, {'width': 319}, {'height': 479}, {'opacity': .5},
    {'radius': 12}, {'stroke_width': 1}, {'text': '还有排版文字'},
])
def test_any_nonidentity_layer_requires_real_renderer(changes):
    doc, _ = document()
    doc.layers[0] = doc.layers[0].model_copy(update=changes)
    assert try_full_frame_png(doc) is None


@pytest.mark.parametrize('visible', [True, False])
def test_extra_text_or_hidden_layer_cannot_be_skipped(visible):
    doc, _ = document()
    doc.layers.append(PosterLayer(id='additional', type='text', x=0, y=0, width=100, height=40,
                                  text='必须保留', visible=visible))
    assert try_full_frame_png(doc) is None


@pytest.mark.parametrize('raw', [b'\x89PNG\r\n\x1a\ninvalid', image_bytes(size=(640, 960)),
                                image_bytes(transparent=True), image_bytes(format='JPEG')])
def test_invalid_resized_transparent_or_non_png_does_not_bypass_composition(raw):
    doc, _ = document(raw)
    assert try_full_frame_png(doc) is None


def test_canvas_mismatch_and_asset_digest_mismatch_require_renderer():
    doc, _ = document()
    doc.canvas.height = 481
    assert try_full_frame_png(doc) is None
    doc, _ = document()
    doc.assets['finished']['sha256'] = '0' * 64
    assert try_full_frame_png(doc) is None


def test_source_callback_and_mapping_are_resolved_without_fetching_urls():
    doc, raw = document()
    assert try_full_frame_png(doc, lambda identifier: raw) == raw
    assert try_full_frame_png(doc, {'finished': raw}) == raw
    assert try_full_frame_png(doc, {'finished': 'https://example.com/finished.png'}) is None


def test_future_crop_or_rotation_and_unknown_dict_fields_are_conservatively_rejected():
    doc, _ = document()
    doc.assets['finished']['crop'] = [0, 0, 100, 100]
    assert try_full_frame_png(doc) is None
    doc, _ = document()
    value = doc.model_dump()
    value['layers'][0]['rotation'] = 90
    assert try_full_frame_png(value) is None
