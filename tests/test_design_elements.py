import asyncio
import base64
from copy import deepcopy
import hashlib
import io
from types import SimpleNamespace

from PIL import Image
import pytest

from app.design.elements import attach_design_elements, generate_design_elements, plan_design_elements
from app.poster import PosterBrief, PosterCanvas, PosterDocument, PosterLayer


def png(color, size):
    out = io.BytesIO()
    Image.new('RGBA', size, color).save(out, 'PNG')
    return out.getvalue()


def fixture(width=320, height=480):
    raw = png((20, 140, 130, 255), (80, 120))
    subject = {'data_url': 'data:image/png;base64,'+base64.b64encode(raw).decode(),
               'sha256': hashlib.sha256(raw).hexdigest(), 'source_id': 'REAL-SOURCE-CANARY'}
    doc = PosterDocument(width=width, height=height, canvas=PosterCanvas(width=width, height=height),
        brief=PosterBrief(width=width, height=height), assets={'phone': subject},
        layers=[PosterLayer(id='native-phone', type='image', role='product', asset_id='phone',
                            x=20, y=100, width=160, height=240, locked=True),
                PosterLayer(id='native-title', type='text', role='headline',
                            x=20, y=20, width=200, height=60, text='COPY-CANARY-不传给图片模型')])
    plan = {'selected_concept_id': 'one', 'concepts': [{
        'id': 'one', 'art_direction': {'key_visual': '抽象取景关系',
        'material_and_light': '柔和金属与渐变光', 'spatial_hierarchy': '主体前置'},
        'background': '#102020', 'foreground': '#FFFFFF', 'accent': '#55AAAA'}]}
    board = SimpleNamespace(concept_id='one', document=doc, visual_brief='以光的轨迹连接两个留白区域')
    calls = []
    class Service:
        async def generate_background(self, prompt, w, h):
            calls.append((prompt, w, h))
            return SimpleNamespace(data=png((30, 35, 70, 255), (64, 96)),
                                   model='actual-test-model', request_id='request-trace')
    return plan, board, Service(), calls


def generated(plan, board, service):
    class Director:
        enabled = True
        async def _json(self, system, payload):
            return {'motif': '交错光路形成收束节奏', 'material': '细腻雾面色带',
                'light': '侧向柔光，避免标题留白区高亮', 'spatial_instructions': '光路在留空区外围疏密相接',
                'native_layer_ids': [layer.id for layer in board.document.layers if layer.visible and layer.role != 'design_element']}
    async def execute():
        brief = await plan_design_elements(plan, board, Director())
        return await generate_design_elements(plan, board, service, element_brief=brief)
    return asyncio.run(execute())


def test_generator_receives_art_direction_and_coordinates_without_source_pixels_or_copy():
    plan, board, service, calls = fixture()
    meta = generated(plan, board, service)
    assert len(calls) == 1
    prompt, w, h = calls[0]
    assert (w, h) == (1024, 1536)
    assert '交错光路形成收束节奏' in prompt
    assert '抽象取景关系' not in prompt and '以光的轨迹' not in prompt
    assert 'reserved_layout_in_pixels' in prompt and 'source_subject' in prompt
    assert 'COPY-CANARY' not in prompt and 'REAL-SOURCE-CANARY' not in prompt and 'data:image' not in prompt
    assert 'NO real streets' in prompt and 'actual camera samples' in prompt
    assert 'Do not render or redraw any product' in prompt
    assert 'key_visual' not in prompt and 'spatial_hierarchy' not in prompt and 'visual_brief' not in prompt
    assert meta['provenance'] == 'ai_generated_design_elements'
    assert meta['model'] == 'actual-test-model' and meta['request_id'] == 'request-trace'
    assert meta['requested_size'] == [1024, 1536] and meta['generated_size'] == [64, 96]
    assert meta['normalization'] == 'center_cover_resize_crop_design_elements_only'
    raw = base64.b64decode(meta['data_url'].split(',', 1)[1])
    assert hashlib.sha256(raw).hexdigest() == meta['sha256']
    assert Image.open(io.BytesIO(raw)).size == (320, 480)


@pytest.mark.parametrize('size,expected_size', [((1080, 1080), (1024, 1024)), ((640, 400), (1536, 1024))])
def test_generation_uses_supported_model_sizes(size, expected_size):
    plan, board, service, calls = fixture(*size)
    generated(plan, board, service)
    assert calls[0][1:] == expected_size


def test_attached_generated_layer_keeps_real_subject_and_type_separate_and_identical():
    plan, board, service, _ = fixture()
    original = board.document.model_dump()
    meta = generated(plan, board, service)
    result = attach_design_elements(board.document, meta)
    assert board.document.model_dump() == original
    assert [layer.model_dump() for layer in result.layers[1:]] == original['layers']
    assert result.assets['phone'] == original['assets']['phone']
    layer = result.layers[0]
    assert layer.role == 'design_element' and layer.type == 'image' and layer.locked
    assert layer.z == -20 and layer.width == result.width and layer.height == result.height
    assert result.assets[layer.asset_id]['sha256'] == meta['sha256']
    repeated = attach_design_elements(result, meta)
    assert repeated.model_dump() == result.model_dump()


def test_replacement_changes_only_generated_design_layer():
    plan, board, service, _ = fixture()
    meta = generated(plan, board, service)
    original = attach_design_elements(board.document, meta)
    replacement = deepcopy(meta)
    raw = png((120, 20, 30, 255), (320, 480))
    replacement.update(sha256=hashlib.sha256(raw).hexdigest(),
        data_url='data:image/png;base64,'+base64.b64encode(raw).decode())
    replacement['id'] = 'design-elements-'+replacement['sha256'][:20]
    result = attach_design_elements(original, replacement)
    assert len([layer for layer in result.layers if layer.role == 'design_element']) == 1
    assert meta['id'] not in result.assets
    assert result.assets['phone'] == board.document.assets['phone']
    assert [layer.model_dump() for layer in result.layers[1:]] == [layer.model_dump() for layer in board.document.layers]


@pytest.mark.parametrize('field,value,code', [
    ('provenance', 'real_store_photo', 'invalid_design_element_provenance'),
    ('sha256', '0'*64, 'design_element_hash_mismatch'),
    ('id', 'phone', 'design_element_id_mismatch'),
])
def test_untrusted_design_element_metadata_rejected(field, value, code):
    plan, board, service, _ = fixture()
    meta = generated(plan, board, service)
    meta[field] = value
    with pytest.raises(ValueError, match=code):
        attach_design_elements(board.document, meta)


def test_wrong_concept_never_calls_service():
    plan, board, service, calls = fixture()
    board.concept_id = 'other'
    with pytest.raises(ValueError, match='concept_mismatch'):
        generated(plan, board, service)
    assert calls == []


def test_full_poster_brief_is_semantically_translated_before_image_generation():
    plan, board, service, image_calls = fixture()
    plan['concepts'][0]['art_direction']['key_visual'] = 'POSTER-CANARY-两台手机与广告标题'
    board.visual_brief = 'PAGE-CANARY-产品背面与人物街景'
    board.document.layers.append(PosterLayer(id='native-curve', type='shape', role='design_shape',
        x=0, y=100, width=200, height=200, geometry='path', path_d='M0 0 L100 100', fill='#114455'))
    translations = []
    class Director:
        enabled = True
        async def _json(self, system, payload):
            translations.append(payload)
            assert '不是重新描述整张广告' in system
            assert payload['context']['native_layout']['already_native_graphics'][0]['path_d'] == 'M0 0 L100 100'
            assert 'POSTER-CANARY' in json.dumps(payload, ensure_ascii=False)
            return {'motif': '局部光束交错，形成抽象节奏', 'material': '柔和纸面与颗粒过渡',
                'light': '侧向冷暖柔光', 'spatial_instructions': '光束避让标题与主体保留区',
                'native_layer_ids': ['native-curve', 'native-title', 'native-phone']}
    async def execute():
        brief = await plan_design_elements(plan, board, Director())
        return await generate_design_elements(plan, board, service, element_brief=brief)
    import json
    meta = asyncio.run(execute())
    assert len(translations) == len(image_calls) == 1
    prompt = image_calls[0][0]
    assert '局部光束交错' in prompt
    assert 'POSTER-CANARY' not in prompt and 'PAGE-CANARY' not in prompt
    assert 'COPY-CANARY' not in prompt and 'data:image' not in prompt
    assert meta['element_direction_version'] == 'element_direction.v2'


def test_no_specialized_direction_means_no_paid_generic_fallback():
    plan, board, service, calls = fixture()
    with pytest.raises(ValueError, match='design_element_direction_required'):
        asyncio.run(generate_design_elements(plan, board, service))
    assert calls == []


def test_translator_cannot_invent_native_layer_responsibility():
    plan, board, service, calls = fixture()
    class BadDirector:
        async def _json(self, system, payload):
            return {'motif': '抽象线带', 'material': '磨砂', 'light': '柔光',
                    'spatial_instructions': '交错', 'native_layer_ids': ['nonexistent-native-layer']}
    with pytest.raises(ValueError, match='unknown_design_element_native_layer'):
        asyncio.run(plan_design_elements(plan, board, BadDirector()))
    assert calls == []


def test_full_canvas_undercoat_moves_behind_generated_plate_without_deleting_author_layer():
    from app.design.renderer import render_design_svg
    from app.poster_render import render_png
    plan, board, service, _ = fixture()
    undercoat = PosterLayer(id='author-paper', type='shape', role='design_shape',
        x=0, y=0, width=320, height=480, z=0, geometry='rect', fill='#FAFAFA')
    board.document.layers.insert(0, undercoat)
    original_layer = undercoat.model_dump()
    original_native = [layer.model_dump() for layer in board.document.layers[1:]]
    meta = generated(plan, board, service)
    result = attach_design_elements(board.document, meta)
    actual = next(layer for layer in result.layers if layer.id == 'author-paper')
    assert actual.model_dump() == {**original_layer, 'z': -21}
    assert actual.role == 'design_shape' and undercoat.z == 0
    assert [layer.model_dump() for layer in result.layers if layer.id in {'native-phone', 'native-title'}] == original_native
    record = result.assets[meta['id']]['attachment_adjustments']
    assert record == [{'layer_id': 'author-paper', 'kind': 'background_undercoat_relayer',
        'original_z': 0, 'applied_z': -21,
        'reason': 'opaque_full_canvas_paint_below_generated_design_layer'}]
    repeated = attach_design_elements(result, meta)
    assert repeated.model_dump() == result.model_dump()
    # Actual pixels confirm the element layer is no longer covered by white paint.
    raw = render_png(render_design_svg(result), 320, 480)
    with Image.open(io.BytesIO(raw)) as image:
        assert image.convert('RGB').getpixel((5, 5)) == (30, 35, 70)


@pytest.mark.parametrize('changes', [
    {'width': 319}, {'x': 1}, {'opacity': .99}, {'rotation': 1}, {'stroke_width': 1},
    {'radius': 10}, {'shadow_opacity': .1}, {'text': 'meaningful label'},
    {'fact_ids': ['claim']}, {'asset_id': 'source'},
])
def test_foreground_local_or_adorned_graphics_keep_authored_z(changes):
    plan, board, service, _ = fixture()
    layer = PosterLayer(id='author-graphic', type='shape', role='design_shape',
        x=0, y=0, width=320, height=480, z=4, geometry='rect', fill='#336677')
    layer = PosterLayer.model_validate({**layer.model_dump(), **changes})
    board.document.layers.insert(0, layer)
    expected = layer.model_dump()
    meta = generated(plan, board, service)
    result = attach_design_elements(board.document, meta)
    assert next(item for item in result.layers if item.id == layer.id).model_dump() == expected
    assert result.assets[meta['id']]['attachment_adjustments'] == []


def test_already_authored_below_background_stays_at_original_z():
    plan, board, service, _ = fixture()
    layer = PosterLayer(id='deep-paper', type='shape', x=0, y=0, width=320, height=480,
                        z=-30, fill='#334455')
    board.document.layers.insert(0, layer)
    meta = generated(plan, board, service)
    result = attach_design_elements(board.document, meta)
    assert next(item for item in result.layers if item.id == layer.id).z == -30
    assert result.assets[meta['id']]['attachment_adjustments'] == []
