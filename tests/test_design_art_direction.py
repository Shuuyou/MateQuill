import asyncio
import base64
import io

import pytest
from PIL import Image

from app.design_art_direction import create_commercial_artboard, DESIGN_REVIEW_PROTOCOL_VERSION
from app.image_service import ImageArtifact, ImageServiceError


def protocol_result(request, checks):
    import json
    body = json.loads(request.content)
    context = json.loads(body['messages'][1]['content'][0]['text'])
    text = next(item for item in checks if item['category'] == 'text_reference')
    text['text_observations'] = [{'source_text_id': row['source_text_id'], 'expected': row['text'],
        'observed': row['text'], 'location': '画面上方', 'status': 'match'} for row in context['display_copy_verbatim']]
    text['unexpected_text'] = []
    design = next(item for item in checks if item['category'] == 'design')
    if 'task_observation' in design:
        design['task_location'] = '画面右侧'
    return {'protocol_version': DESIGN_REVIEW_PROTOCOL_VERSION, 'review_completed': True, 'checks': checks}


def png(color='red', size=(64, 64)):
    stream = io.BytesIO()
    Image.new('RGB', size, color).save(stream, 'PNG')
    return stream.getvalue()


def assets():
    return {'front': {'product': 'HUAWEI Example', 'view': 'front',
                      'data_url': 'data:image/png;base64,' + base64.b64encode(png('red')).decode(),
                      'url': 'https://private.example/CANARY'},
            'rear': {'product': 'HUAWEI Example', 'view': 'rear',
                     'data_url': 'data:image/png;base64,' + base64.b64encode(png('blue')).decode()}}


class FakeService:
    def __init__(self, error=None):
        self.calls = []
        self.error = error

    async def edit(self, prompt, image_bytes, *, reference_images):
        self.calls.append((prompt, image_bytes, reference_images))
        if self.error:
            raise self.error
        return ImageArtifact(png(), 'image/png', 'actual-image-model', 'request-1', 64, 64, 320, 480,
                             'ai_edited_candidate')


def generate(service, **changes):
    args = dict(width=320, height=480, brief={'product': 'HUAWEI Example', 'publisher_kind': 'store',
        'publisher_label': '演示门店', 'platform': {'platform': '小红书'}},
        concept={'id': 'editorial', 'visual_thesis': '通过真实产品细节与精简字体营造发现感',
                 'page_asset_ids': ['rear', 'front'], 'background': '#102020'},
        role='cover', page_index=0, texts={'title': {'text': '从一个新视角发现它'},
        'fact-0': {'text': '功能A：仅适用于支持的配置'}}, assets=assets())
    args.update(changes)
    return asyncio.run(create_commercial_artboard(service, **args))


def test_full_frame_design_uses_blank_target_and_actual_ordered_references():
    client = FakeService()
    result = generate(client)
    assert len(client.calls) == 1
    prompt, canvas, references = client.calls[0]
    with Image.open(io.BytesIO(canvas)) as image:
        assert image.size == (320, 480)
        assert len(image.getcolors()) == 1  # no old pasted product/template
    with Image.open(io.BytesIO(references[0])) as image:
        assert image.getpixel((1, 1)) == (0, 0, 255, 255)  # requested rear first
    assert '完整' in prompt and '中文字体' in prompt and '矩形底板' in prompt
    assert '仅适用于支持的配置' in prompt
    assert 'private.example' not in prompt and 'CANARY' not in prompt and 'data:image' not in prompt
    assert result.provenance['source_asset_ids'] == ['rear', 'front']
    assert result.provenance['fidelity_status'] == 'reference_guided_unverified'
    assert result.provenance['text_rendering_status'] == 'generated_requires_review'
    assert result.provenance['actual_size'] == [64, 64]
    assert result.artifact.model == 'actual-image-model'


def test_product_references_do_not_license_store_scenes_or_camera_samples():
    import json
    service = FakeService()
    result = generate(service)
    prompt = service.calls[0][0]
    context = json.loads(prompt.split('<design_context>', 1)[1].split('</design_context>', 1)[0])
    contract = context['scene_origin_contract']
    assert contract['store_photo_available'] is False
    assert '只证明外观' in contract['product_reference_scope']
    assert '样片对比' in contract['sample_scope']
    assert '情绪' in contract['creative_scope']
    assert result.provenance['scene_origin_contract'] == contract


def test_page_role_platform_and_identity_direct_art_direction():
    client = FakeService()
    generate(client, role='detail', page_index=1, brief={'product': 'P', 'publisher_kind': 'brand',
        'platform': {'platform': '朋友圈'}})
    prompt = client.calls[0][0]
    assert '第2页' in prompt
    assert '只放大参考图中真实可见' in prompt
    assert '面向熟人社交' in prompt
    assert '品牌任务' in prompt
    assert '不发明价格' in prompt


def test_provider_failure_does_not_trigger_paid_retry():
    client = FakeService(ImageServiceError('timeout', reason='timeout'))
    with pytest.raises(ImageServiceError):
        generate(client)
    assert len(client.calls) == 1


def test_reference_data_required_no_external_url_fetch():
    client = FakeService()
    with pytest.raises(ImageServiceError):
        generate(client, assets={'x': {'url': 'https://private.example/photo.png'}})
    assert client.calls == []


@pytest.mark.parametrize('changes', [{'width': True}, {'height': 1}, {'page_index': -1}, {'page_index': True}])
def test_invalid_canvas_fails_before_provider(changes):
    client = FakeService()
    with pytest.raises(ImageServiceError):
        generate(client, **changes)
    assert not client.calls


def test_at_most_four_product_reference_images():
    client = FakeService()
    supplied = {str(index): {'data_url': 'data:image/png;base64,' + base64.b64encode(png()).decode()}
                for index in range(9)}
    result = generate(client, assets=supplied)
    assert len(client.calls[0][2]) == 4
    assert len(result.provenance['source_asset_ids']) == 4


def test_display_copy_is_exact_short_source_substring_and_not_long_factual_paragraph():
    client = FakeService()
    result = generate(client, texts={'title': {'text': '一个长标题的开头，换一个角度看见它，后续冗长营销说明超过单张画面需要'},
                                    'block-0': {'text': '已核实功能仅适用于指定配置与支持版本；请完整保留这些限定条件，不能从中裁半句生成短标题。'}})
    display = result.provenance['display_copy']
    assert display and all(len(row['text']) <= 32 for row in display)
    assert all(row['source_text_id'] == 'title' for row in display)
    assert '逐字使用display_copy_verbatim_only' in client.calls[0][0]


def test_transparent_padding_is_removed_from_product_references():
    source = Image.new('RGBA', (600, 400), (255, 255, 255, 0))
    source.paste(Image.new('RGBA', (60, 200), 'red'), (200, 100))
    stream = io.BytesIO()
    source.save(stream, 'PNG')
    client = FakeService()
    generate(client, assets={'cutout': {'data_url': 'data:image/png;base64,' + base64.b64encode(stream.getvalue()).decode()}})
    with Image.open(io.BytesIO(client.calls[0][2][0])) as reference:
        assert reference.size == (60, 200)


def test_real_output_review_compares_pixels_with_product_references(monkeypatch):
    import json
    import httpx
    from app.design_art_direction import review_commercial_artboard
    result = generate(FakeService())
    actual = httpx.AsyncClient
    def handler(request):
        body = json.loads(request.content)
        images = [item for item in body['messages'][1]['content'] if item['type'] == 'image_url']
        assert len(images) == 3  # actual output plus two real references
        checks = [{'category': category, 'severity': 'warning' if category == 'product_reference' else 'info',
                   'message': '实际画面的观察：' + category}
                  for category in ('text_reference', 'product_reference', 'publication', 'design')]
        return httpx.Response(200, json={'choices': [{'message': {'content': json.dumps(protocol_result(request, checks))}}]})
    monkeypatch.setattr(httpx, 'AsyncClient', lambda **kw: actual(transport=httpx.MockTransport(handler), **kw))
    review = asyncio.run(review_commercial_artboard(result.artifact, base_url='https://vision.example/v1', key='secret',
        model='vision', assets=assets(), provenance=result.provenance))
    assert review['review_completed'] and review['reference_comparison_completed']
    assert not review['product_identity_verified']
    assert not review['can_publish']
    assert review['critical_uncertainties'][0]['category'] == 'product_reference'


def test_incomplete_product_comparison_does_not_pass(monkeypatch):
    import httpx
    from app.design_art_direction import review_commercial_artboard
    result = generate(FakeService())
    actual = httpx.AsyncClient
    monkeypatch.setattr(httpx, 'AsyncClient', lambda **kw: actual(transport=httpx.MockTransport(
        lambda request: httpx.Response(200, json={'choices': [{'message': {'content': '{}'}}]})), **kw))
    review = asyncio.run(review_commercial_artboard(result.artifact, base_url='https://vision.example/v1', key='secret',
        model='vision', assets=assets(), provenance=result.provenance))
    assert not review['review_completed'] and not review['reference_comparison_completed']


@pytest.mark.parametrize('instruction', ['不要任何文字', '这组图无字', '不要加文字', '去掉所有文字', 'no text please'])
def test_explicit_no_text_overrides_all_source_copy(instruction):
    client = FakeService()
    result = generate(client, brief={'instruction': instruction, 'product': 'P'})
    assert result.provenance['display_copy'] == []
    assert result.provenance['text_policy'] == 'no_overlay_text'
    assert '本次明确要求无文字' in client.calls[0][0]


@pytest.mark.parametrize('row', [
    {'text': '续航支持二十小时连续使用；仅限实验室条件和指定配置版本'},
    {'text': '一个足够长的功能表现标题；这后面是不可丢掉的重要适用说明', 'fact_ids': ['fact-1']},
    {'text': '一个足够长的功能表现标题；这后面是不可丢掉的重要适用说明', 'qualifiers': ['限指定配置']},
])
def test_qualified_headings_are_atomic_and_never_split_at_semicolon(row):
    result = generate(FakeService(), texts={'headline': row})
    assert result.provenance['display_copy'] == []


def test_directional_copy_sets_required_main_view_for_creation_and_review(monkeypatch):
    import json
    import httpx
    from app.design_art_direction import review_commercial_artboard
    client = FakeService()
    result = generate(client, texts={'title': {'text': '先看侧面'}})
    assert result.provenance['required_main_view'] == 'side'
    assert '侧面只是细小陪衬' not in client.calls[0][0]  # creation has its own positive main-view requirement
    assert 'required_main_view' in client.calls[0][0]
    actual = httpx.AsyncClient
    def handler(request):
        body = json.loads(request.content)
        context = json.loads(body['messages'][1]['content'][0]['text'])
        assert context['required_main_view'] == 'side'
        assert '侧面只是细小陪衬也应标error' in body['messages'][0]['content']
        checks = [{'category': category, 'severity': 'error' if category == 'product_reference' else 'info',
                   'message': '侧面标题对应的主视觉却是背面' if category == 'product_reference' else '观察'}
                  for category in ('text_reference', 'product_reference', 'publication', 'design')]
        return httpx.Response(200, json={'choices': [{'message': {'content': json.dumps(protocol_result(request, checks))}}]})
    monkeypatch.setattr(httpx, 'AsyncClient', lambda **kw: actual(transport=httpx.MockTransport(handler), **kw))
    review = asyncio.run(review_commercial_artboard(result.artifact, base_url='https://vision.example/v1', key='secret',
        model='vision', assets=assets(), provenance=result.provenance))
    assert review['has_errors'] and not review['product_identity_verified']


def test_series_cover_style_reference_keeps_product_main_first_and_total_max_four():
    client = FakeService()
    sources = {str(index): {'data_url': 'data:image/png;base64,' + base64.b64encode(png('red')).decode()}
               for index in range(5)}
    result = generate(client, assets=sources, brief={'product': 'P', 'series_reference': {
        'data_url': 'data:image/png;base64,' + base64.b64encode(png('green')).decode()}})
    assert len(client.calls[0][2]) == 4
    assert result.provenance['source_asset_ids'] == ['0', '1', '2']
    assert result.provenance['series_reference_sha256']
    with Image.open(io.BytesIO(client.calls[0][2][0])) as primary:
        assert primary.getpixel((1, 1)) == (255, 0, 0, 255)
    with Image.open(io.BytesIO(client.calls[0][2][-1])) as style:
        assert style.getpixel((1, 1)) == (0, 128, 0, 255)


def test_brand_identity_and_store_task_shape_visual_without_inventing_display_copy():
    import json
    client = FakeService()
    supplied = assets()
    supplied['store-photo'] = {'data_url': 'data:image/png;base64,' + base64.b64encode(png('green')).decode()}
    supplied['brand-kv'] = {'data_url': 'data:image/png;base64,' + base64.b64encode(png('yellow')).decode()}
    brief = {'product': 'P', 'publisher_kind': 'store', 'brand_identity': {
        'brand': 'HUAWEI', 'principles': ['基于授权新品主视觉的几何秩序'],
        'reference_asset_ids': ['brand-kv'], 'source_signals': [{'observed': '整体黑白层次', 'url': 'https://private.example/key'}]},
        'store_context': {'reader_task': '现场比较屏幕显示与握持感', 'service_angle': '带自己的常用图片来试',
            'photo_asset_ids': ['store-photo'], 'audience_hypotheses': [{'kind': 'hypothesis', 'text': '周边可能有学生'}]},
        'series_reference': {'data_url': 'data:image/png;base64,' + base64.b64encode(png('purple')).decode()}}
    result = generate(client, brief=brief, assets=supplied)
    prompt = client.calls[0][0]
    context = json.loads(prompt.split('<design_context>')[1].split('</design_context>')[0])
    assert context['store_context']['reader_task'] == '现场比较屏幕显示与握持感'
    assert context['brand_identity']['principles'] == ['基于授权新品主视觉的几何秩序']
    assert 'https://private.example/key' not in prompt
    assert 'data:image' not in prompt
    assert context['display_copy_verbatim_only'][0]['text'] == '从一个新视角发现它'
    assert '门店相关性必须进入主视觉任务' in prompt
    assert len(client.calls[0][2]) == 4
    assert [item['kind'] for item in result.provenance['auxiliary_references']] == ['store_photo', 'brand_identity', 'series_style']
    assert result.provenance['source_asset_ids'] == ['rear']


def test_brand_reference_does_not_replace_explicit_primary_product_view():
    client = FakeService()
    result = generate(client, brief={'brand_identity': {'reference_asset_ids': ['rear']}})
    assert result.provenance['source_asset_ids'][0] == 'rear'
    assert result.provenance['primary_reference_roles'] == ['product', 'brand_identity']
    assert all(item['asset_id'] != 'rear' for item in result.provenance['auxiliary_references'])
    with Image.open(io.BytesIO(client.calls[0][2][0])) as primary:
        assert primary.getpixel((0, 0)) == (0, 0, 255, 255)


def test_brand_and_store_review_observes_actual_auxiliary_images_without_style_certification(monkeypatch):
    import json
    import httpx
    from app.design_art_direction import review_commercial_artboard
    supplied = assets()
    supplied['brand-kv'] = {'data_url': 'data:image/png;base64,' + base64.b64encode(png('yellow')).decode()}
    supplied['store-photo'] = {'data_url': 'data:image/png;base64,' + base64.b64encode(png('green')).decode()}
    result = generate(FakeService(), assets=supplied, brief={'publisher_kind': 'store',
        'brand_identity': {'reference_asset_ids': ['brand-kv']},
        'store_context': {'reader_task': '比较握持感', 'photo_asset_ids': ['store-photo']}})
    actual = httpx.AsyncClient
    def handler(request):
        body = json.loads(request.content)
        context = json.loads(body['messages'][1]['content'][0]['text'])
        images = [item for item in body['messages'][1]['content'] if item['type'] == 'image_url']
        assert len(images) == 5  # output + two product refs + brand/store evidence
        assert context['store_context']['reader_task'] == '比较握持感'
        assert '页脚地址/地名贴纸' in body['messages'][0]['content']
        assert '无实际参考时不能认证品牌风格' in body['messages'][0]['content']
        checks = [{'category': category, 'severity': 'info', 'message': '画面中可见的对应关系'}
                  for category in ('text_reference', 'product_reference', 'publication', 'design')]
        checks[-1].update(task_visible=True, task_observation='画面中央手持主体与左侧局部形成握持尺度对照')
        return httpx.Response(200, json={'choices': [{'message': {'content': json.dumps(protocol_result(request, checks))}}]})
    monkeypatch.setattr(httpx, 'AsyncClient', lambda **kw: actual(transport=httpx.MockTransport(handler), **kw))
    review = asyncio.run(review_commercial_artboard(result.artifact, base_url='https://vision.example/v1', key='secret',
        model='vision', assets=supplied, provenance=result.provenance))
    assert review['brand_reference_supplied'] and review['store_photo_reference_supplied']
    assert not review['brand_identity_certified']
    assert len(review['checks']) == 4


def test_cover_product_identity_is_visible_exact_name_separate_from_editorial_copy():
    client = FakeService()
    result = generate(client, texts={'design-copy-cover-headline': {'text': '带上常用照片来看看'},
        'design-copy-cover-caption': {'text': '比较屏幕与握持感'},
        'identity-product-label': {'text': 'HUAWEI Mate 90 Pro Max', 'role': 'product_identity'}})
    display = result.provenance['display_copy']
    assert len(display) == 3
    assert display[-1] == {'role': 'product_identity', 'text': 'HUAWEI Mate 90 Pro Max',
                          'source_text_id': 'identity-product-label', 'required': False, 'scope': 'standalone_overlay'}
    assert display[0]['role'] == 'headline' and display[1]['role'] == 'caption'
    assert '不是角落极小脚注' in client.calls[0][0]
    assert '不仿造或重新设计品牌Logo' in client.calls[0][0]


@pytest.mark.parametrize('role', ['cover', 'hero'])
def test_long_product_identity_is_atomic_and_not_cut_into_headline(role):
    result = generate(FakeService(), role=role, texts={
        'identity-product-label': {'text': 'HUAWEI MateBook 14 高能版 2026 官方准确配置名称'},
        'headline': {'text': '换个角度看见它'}})
    identity = next(row for row in result.provenance['display_copy'] if row['role'] == 'product_identity')
    assert identity['text'] == 'HUAWEI MateBook 14 高能版 2026 官方准确配置名称'
    assert result.provenance['display_copy'][0]['text'] == '换个角度看见它'


@pytest.mark.parametrize('role', ['detail', 'invitation', 'photo'])
def test_later_pages_do_not_repeat_cover_identity(role):
    result = generate(FakeService(), role=role, texts={'identity-product-label': {'text': 'HUAWEI Mate90'},
        'headline': {'text': '换个角度看见它'}})
    assert not any(row['role'] == 'product_identity' for row in result.provenance['display_copy'])
    assert all(row['text'] != 'HUAWEI Mate90' for row in result.provenance['display_copy'])


def test_no_text_also_removes_product_identity_overlay():
    result = generate(FakeService(), brief={'instruction': '不要任何文字'}, texts={
        'identity-product-label': {'text': 'HUAWEI Mate90'}, 'headline': {'text': '换个角度看见它'}})
    assert result.provenance['display_copy'] == []


def test_approved_long_caption_reaches_image_and_review_manifest_before_legacy_intro():
    caption = '到教工路百脑汇看看机身背面与边框的衔接，再留意圆角和手指接触的位置。'
    assert 32 < len(caption) <= 44
    client = FakeService()
    result = generate(client, texts={
        'intro': {'text': '旧稿中的另一条短补充'},
        'design-copy-0-headline': {'text': '在百脑汇，先看这处衔接'},
        'design-copy-0-caption': {'text': caption},
    })
    displayed = result.provenance['display_copy']
    assert next(row for row in displayed if row['role'] == 'caption')['text'] == caption
    assert not any(row['text'] == '旧稿中的另一条短补充' for row in displayed)
    assert caption in client.calls[0][0]


def test_large_authorized_store_memory_is_compacted_before_paid_generation():
    import json
    from copy import deepcopy
    client = FakeService()
    quote = '已授权来源中的本地观察。' * 80
    store = {'reader_task': '带自己的照片比较屏幕', 'service_angle': '比较同一张照片的呈现',
        'evidence': [{'id': f'e-{i}', 'kind': 'observation', 'quote': quote,
                      'source_records': [{'quote': quote}] * 12, 'resource': {'secret': 'not sent'}} for i in range(60)],
        'local_signals': [{'kind': 'hypothesis', 'text': quote}] * 60,
        'creative_opportunities': [{'task': '比较屏幕', 'reason': quote}] * 60,
        'workflow_preferences': ['完整历史记录'] * 60, 'full_memory_catalog': [quote] * 60}
    brand = {'brand': 'HUAWEI', 'principles': ['几何秩序与清楚产品身份'] * 20,
             'typography': {'headline': '清晰、克制'}, 'palette': {'direction': '从真实产品提取'},
             'source_signals': [{'kind': 'authorized_material', 'observed': quote}] * 60,
             'full_catalog': [quote] * 60}
    original = deepcopy(store)
    texts = {'identity-product-label': {'text': 'HUAWEI Mate90'}, 'headline': {'text': '带照片来看看'},
             **{f'fact-{i}': {'text': '测试结果，完整限定条件必须保留；' + '仅限已说明的配置条件。' * 100,
                              'fact_ids': [f'f-{i}']} for i in range(20)}}
    result = generate(client, brief={'brand_identity': brand, 'store_context': store}, texts=texts)
    prompt = client.calls[0][0]
    context = json.loads(prompt.split('<design_context>')[1].split('</design_context>')[0])
    assert len(prompt) <= 16000
    assert len(context['store_context'].get('evidence', [])) <= 3
    assert len(context['store_context'].get('local_signals', [])) <= 2
    assert len(context['brand_identity'].get('source_signals', [])) <= 2
    assert 'full_memory_catalog' not in prompt and 'source_records' not in prompt
    assert context['store_context']['reader_task'] == '带自己的照片比较屏幕'
    assert result.provenance['display_copy'][-1]['text'] == 'HUAWEI Mate90'
    # Any admitted source is whole, including every qualifier; full server data is untouched.
    for row in context['approved_copy_sources_for_context_only']:
        assert row['text'] == texts[row['id']]['text']
    assert store == original


def test_qualified_headline_keeps_required_caption_longer_than_old_limit():
    caption = '仅在实验室条件下测得，实际结果因使用环境与配置不同而变化，请以实际体验为准'
    assert 32 < len(caption) <= 44
    result = generate(FakeService(), texts={
        'design-copy-0-headline': {'text': '测试数据20小时', 'fact_ids': ['f1'],
                                 'required_text_ids': ['design-copy-0-caption']},
        'design-copy-0-caption': {'text': caption, 'fact_ids': ['f1']},
        'identity-product-label': {'text': 'HUAWEI Mate90'}})
    display = result.provenance['display_copy']
    assert display[0]['text'] == '测试数据20小时'
    assert display[1]['text'] == caption
    assert display[1]['role'] == 'caption'
    assert len(display) == 3


def test_missing_required_caption_omits_entire_claim():
    result = generate(FakeService(), texts={
        'design-copy-0-headline': {'text': '测试数据20小时', 'fact_ids': ['f1'],
                                 'required_text_ids': ['missing-caption']},
        'identity-product-label': {'text': 'HUAWEI Mate90'}})
    assert all(row['text'] != '测试数据20小时' for row in result.provenance['display_copy'])
    assert result.provenance['display_copy'] == [{'role': 'product_identity', 'text': 'HUAWEI Mate90',
                                                 'source_text_id': 'identity-product-label', 'required': False, 'scope': 'standalone_overlay'}]


def test_photo_cannot_drop_qualifier_caption_from_audited_pair():
    result = generate(FakeService(), role='photo', texts={
        'design-copy-0-headline': {'text': '测试数据20小时', 'fact_ids': ['f1'],
                                 'required_text_ids': ['design-copy-0-caption']},
        'design-copy-0-caption': {'text': '仅在实验室条件下测得，实际结果因使用环境与配置不同而变化，请以实际体验为准'}})
    assert result.provenance['display_copy'] == []


@pytest.mark.parametrize('task_visible,expected_error', [(False, True), (True, False)])
def test_store_task_review_requires_actual_visible_action_or_observation(monkeypatch, task_visible, expected_error):
    import json
    import httpx
    from app.design_art_direction import review_commercial_artboard
    client = FakeService()
    result = generate(client, brief={'store_context': {'reader_task': '拿起手机观察背面纹理', 'service_angle': '握持与局部对照'}})
    assert result.provenance['store_task_requirement']['active']
    assert '纯产品静置加石台、植物、光斑并不算完成' in client.calls[0][0]
    actual = httpx.AsyncClient
    def handler(request):
        body = json.loads(request.content)
        assert 'task_observation' in body['messages'][0]['content']
        assert '整体产品静置' not in body['messages'][0]['content'] or 'task_visible' in body['messages'][0]['content']
        checks = [{'category': category, 'severity': 'info', 'message': '实际成图观察'}
                  for category in ('text_reference', 'product_reference', 'publication', 'design')]
        checks[-1].update(task_visible=task_visible, task_observation=(
            '左上为准确纹理局部放大，并与右下手持产品形成对应' if task_visible else
            '画面只有中央静置整机与无关石台植物，没有握持或局部观察关系'))
        return httpx.Response(200, json={'choices': [{'message': {'content': json.dumps(protocol_result(request, checks))}}]})
    monkeypatch.setattr(httpx, 'AsyncClient', lambda **kw: actual(transport=httpx.MockTransport(handler), **kw))
    review = asyncio.run(review_commercial_artboard(result.artifact, base_url='https://vision.example/v1', key='secret',
        model='vision', assets=assets(), provenance=result.provenance))
    assert review['review_completed']
    assert review['has_errors'] == expected_error
    if expected_error:
        assert review['checks'][-1]['severity'] == 'error'
        assert review['checks'][-1]['issue_code'] == 'missing_task'
        assert not review['can_publish']
    else:
        assert review['can_publish'] and not review['font_identity_certified']


@pytest.mark.parametrize('exact_font_claim', [False, True])
def test_missing_task_observation_or_raster_font_certification_never_passes(monkeypatch, exact_font_claim):
    import json
    import httpx
    from app.design_art_direction import review_commercial_artboard
    result = generate(FakeService(), brief={'store_context': {'reader_task': '看清侧边按键'}})
    actual = httpx.AsyncClient
    def handler(request):
        checks = [{'category': category, 'severity': 'info', 'message': '漂亮产品画面'}
                  for category in ('text_reference', 'product_reference', 'publication', 'design')]
        if exact_font_claim:
            checks[-1].update(message='采用NotoSansSC，符合官方排版', task_visible=True,
                              task_observation='右侧有局部放大')
        return httpx.Response(200, json={'choices': [{'message': {'content': json.dumps(protocol_result(request, checks))}}]})
    monkeypatch.setattr(httpx, 'AsyncClient', lambda **kw: actual(transport=httpx.MockTransport(handler), **kw))
    review = asyncio.run(review_commercial_artboard(result.artifact, base_url='https://vision.example/v1', key='secret',
        model='vision', assets=assets(), provenance=result.provenance))
    assert not review['review_completed']


def test_targeted_revision_passes_exact_existing_artwork_as_edit_input():
    import hashlib
    previous = png('blue', size=(320, 480))
    client = FakeService()
    result = generate(client, brief={'product': 'P', 'instruction': '删除新增优惠文字，保留原构图',
        'revision_image': {'data_url': 'data:image/png;base64,' + base64.b64encode(previous).decode()},
        'correction_checks': [{'category': 'text_reference', 'message': '移除未授权优惠词'}]})
    prompt, actual_input, references = client.calls[0]
    assert actual_input == previous
    assert '必须在这张现有作品上定向编辑' in prompt
    assert '不能重新从空白创作' in prompt
    assert '第一张输入是空白目标画布' not in prompt
    assert '移除未授权优惠词' in prompt
    assert 'data:image' not in prompt
    assert result.provenance['method'] == 'reference_guided_targeted_commercial_revision'
    assert result.provenance['revision_sha256'] == hashlib.sha256(previous).hexdigest()
    assert result.provenance['source_asset_ids'][0] == 'rear'
    with Image.open(io.BytesIO(references[0])) as product:
        assert product.getpixel((0, 0)) == (0, 0, 255, 255)


@pytest.mark.parametrize('revision', [
    {'url': 'https://private.example/old.png'},
    {'data_url': 'data:image/png;base64,invalid'},
    {'data_url': 'data:image/svg+xml;base64,AAAA'},
])
def test_invalid_revision_cannot_silently_degrade_to_blank_regeneration(revision):
    client = FakeService()
    with pytest.raises(ImageServiceError):
        generate(client, brief={'revision_image': revision})
    assert not client.calls


def test_revision_canvas_dimensions_must_match_target_without_distortion():
    client = FakeService()
    with pytest.raises(ImageServiceError):
        generate(client, brief={'revision_image': {'data_url': 'data:image/png;base64,' + base64.b64encode(png()).decode()}})
    assert not client.calls


@pytest.mark.parametrize('failure,expected,code', [
    ('http', 'upstream_http', 429), ('timeout', 'upstream_timeout', None),
    ('network', 'upstream_network', None), ('bad_envelope_json', 'invalid_response_json', None),
    ('bad_review_json', 'invalid_review_json', None), ('bad_schema', 'missing_review_protocol', None),
    ('oversize', 'response_size_limit', None),
])
def test_review_failure_diagnostics_are_safe_specific_and_never_include_provider_body(monkeypatch, failure, expected, code):
    import httpx
    from app.design_art_direction import review_commercial_artboard
    result = generate(FakeService())
    actual = httpx.AsyncClient
    calls = []
    def handler(request):
        calls.append(request)
        if failure == 'http':
            return httpx.Response(429, text='CANARY_SECRET private upstream diagnostic')
        if failure == 'timeout':
            raise httpx.ReadTimeout('CANARY_SECRET', request=request)
        if failure == 'network':
            raise httpx.ConnectError('CANARY_SECRET', request=request)
        if failure == 'bad_envelope_json':
            return httpx.Response(200, text='CANARY_SECRET not json')
        if failure == 'oversize':
            return httpx.Response(200, text='x' * 130000)
        return httpx.Response(200, json={'choices': [{'message': {'content':
            'CANARY_SECRET invalid json' if failure == 'bad_review_json' else '{}'}}]})
    monkeypatch.setattr(httpx, 'AsyncClient', lambda **kw: actual(transport=httpx.MockTransport(handler), **kw))
    review = asyncio.run(review_commercial_artboard(result.artifact, base_url='https://vision.example/v1', key='CANARY_SECRET',
        model='vision', assets=assets(), provenance=result.provenance))
    assert review['review_reason'] == expected
    assert review.get('http_status') == code
    assert not review['review_completed'] and not review['can_publish']
    assert 'CANARY_SECRET' not in str(review) and 'private upstream' not in str(review)
    assert len(calls) == (2 if failure == 'bad_schema' else 1)


@pytest.mark.parametrize('kind,expected', [('font', 'unsupported_font_identity_claim'), ('task', 'missing_task_observation')])
def test_semantic_review_rejection_diagnostics_distinguish_font_claim_and_missing_task(monkeypatch, kind, expected):
    import json
    import httpx
    from app.design_art_direction import review_commercial_artboard
    result = generate(FakeService(), brief={'store_context': {'reader_task': '比较同一张照片'}})
    actual = httpx.AsyncClient
    def handler(request):
        checks = [{'category': category, 'severity': 'info', 'message': '实际画面观察'}
                  for category in ('text_reference', 'product_reference', 'publication', 'design')]
        if kind == 'font':
            checks[-1].update(message='使用Noto Sans SC', task_visible=True, task_observation='中央有照片对照')
        return httpx.Response(200, json={'choices': [{'message': {'content': json.dumps(protocol_result(request, checks))}}]})
    monkeypatch.setattr(httpx, 'AsyncClient', lambda **kw: actual(transport=httpx.MockTransport(handler), **kw))
    review = asyncio.run(review_commercial_artboard(result.artifact, base_url='https://vision.example/v1', key='secret',
        model='vision', assets=assets(), provenance=result.provenance))
    assert review['review_reason'] == expected
    assert not review['review_completed'] and not review['can_publish']


def test_unapproved_second_line_art_direction_is_removed_without_removing_scene():
    import json
    client = FakeService()
    direction = '通勤口袋取放场景，手机与外套口袋形成携带尺度关系。第二行文案写“塞进兜里，再掏出来”。镜头保留真实绿色设备与合理手部尺度。'
    result = generate(client, brief={'store_context': {'reader_task': '了解手机握持与机身细节'},
                                    'page_visual_brief': direction},
        concept={'id': 'pocket', 'visual_thesis': '以真实携带尺度解释产品',
                 'asset_ids': ['rear']}, texts={'headline': {'text': '带着出门的分寸感'}})
    prompt = client.calls[0][0]
    context = json.loads(prompt.split('<design_context>')[1].split('</design_context>')[0])
    assert '塞进兜里，再掏出来' not in prompt
    assert '通勤口袋取放场景' in context['page_visual_brief']
    assert '真实绿色设备与合理手部尺度' in context['page_visual_brief']
    assert result.provenance['store_task_requirement']['page_copy'] == ['带着出门的分寸感']
    assert '通勤口袋取放场景' in result.provenance['store_task_requirement']['page_visual_task']


def test_concept_removes_unapproved_render_copy_but_keeps_approved_type_and_scene_quotes():
    client = FakeService()
    concept = {'id': 'task', 'visual_thesis': '标题使用“带着出门的分寸感”，以大号无衬线形成层级；副行写“塞进兜里，再掏出来”；主体在“外套口袋”边缘，人物动作自然。标题用“无衬线”字体处理。',
               'asset_ids': ['rear']}
    generate(client, concept=concept, texts={'headline': {'text': '带着出门的分寸感'}})
    prompt = client.calls[0][0]
    assert '塞进兜里，再掏出来' not in prompt
    assert '标题使用“带着出门的分寸感”' in prompt
    assert '外套口袋' in prompt
    assert '标题用“无衬线”字体处理' in prompt


def test_store_review_accepts_visible_relevant_page_subtask_without_demanding_all_global_actions(monkeypatch):
    import json
    import httpx
    from app.design_art_direction import review_commercial_artboard
    client = FakeService()
    result = generate(client, brief={'store_context': {'reader_task': '比较手机握持与机身细节', 'service_angle': '观察外观与携带感'},
        'page_visual_brief': '用外套口袋取放动作表现携带尺度，清楚显示真实产品'}, texts={'headline': {'text': '带着出门的分寸感'}})
    actual = httpx.AsyncClient
    def handler(request):
        body = json.loads(request.content)
        context = json.loads(body['messages'][1]['content'][0]['text'])
        requirement = context['store_task_requirement']
        assert requirement['page_copy'] == ['带着出门的分寸感']
        assert '外套口袋取放动作' in requirement['page_visual_task']
        assert '不要求一页完成' in body['messages'][0]['content']
        checks = [{'category': category, 'severity': 'info', 'message': '基于实际成图观察'}
                  for category in ('text_reference', 'product_reference', 'publication', 'design')]
        checks[-1].update(task_visible=True, task_observation='右侧外套口袋与半取出的真实产品形成携带尺度关系，兑现本页携带子任务')
        return httpx.Response(200, json={'choices': [{'message': {'content': json.dumps(protocol_result(request, checks))}}]})
    monkeypatch.setattr(httpx, 'AsyncClient', lambda **kw: actual(transport=httpx.MockTransport(handler), **kw))
    review = asyncio.run(review_commercial_artboard(result.artifact, base_url='https://vision.example/v1', key='secret',
        model='vision', assets=assets(), provenance=result.provenance))
    assert review['review_completed'] and review['can_publish']
    assert not review['has_errors']


def test_brand_transfer_retains_actual_reference_structure_and_prioritizes_task_over_optional_style():
    import json
    supplied = assets()
    supplied['official-kv'] = {'visual_role': 'campaign_key_visual',
        'framing': '主体横向错层，圆环与细线光带形成关系',
        'data_url': 'data:image/png;base64,' + base64.b64encode(png('yellow', size=(120, 60))).decode()}
    client = FakeService()
    result = generate(client, assets=supplied, brief={
        'brand_identity': {'reference_asset_ids': ['official-kv'], 'visual_system': {
            'publisher_adaptation': '用实际体验任务组织画面', 'grid_authority': 'editorial recommendation'}},
        'store_context': {'reader_task': '观察外观纹理的一处细节'}},
        concept={'id': 'editorial', 'asset_ids': ['rear'], 'font_role': 'serif',
                 'visual_thesis': '以杂志字体表达产品'})
    prompt = client.calls[0][0]
    context = json.loads(prompt.split('<design_context>')[1].split('</design_context>')[0])
    brand_reference = next(row for row in context['auxiliary_reference_order'] if row['reference_kind'] == 'brand_identity')
    assert brand_reference['visual_role'] == 'campaign_key_visual'
    assert brand_reference['observed_framing'] == '主体横向错层，圆环与细线光带形成关系'
    assert context['brand_identity']['visual_system']['publisher_adaptation'] == '用实际体验任务组织画面'
    assert context['direction_priority'].index('visible_page_experience_task') < context['direction_priority'].index('optional_concept_style')
    assert '只取粉绿配色、改背景或角落加Logo，不等于完成这种迁移' in prompt
    assert '若KV无文字样本，不推断品牌字体' in prompt
    assert '保留主参考的产品颜色和角度' in prompt
    assert result.provenance['auxiliary_references'][0]['observed_framing'] == brand_reference['observed_framing']


def test_store_visual_direction_does_not_claim_generic_scene_is_real_locality():
    client = FakeService()
    generate(client, brief={'store_context': {'reader_task': '观察机身纹理', 'photo_asset_ids': []}})
    prompt = client.calls[0][0]
    assert '无照片时做中性的任务示意或产品观察图' in prompt
    assert '不生成貌似本店实景的空间' in prompt
    assert '动作或局部必须为判断提供信息，不是装饰' in prompt
    assert '只需兑现一个相关子任务' in prompt


def run_protocol_review(monkeypatch, mutate=None, *, texts=None, task=False, repairs=0):
    import json
    import httpx
    from app.design_art_direction import review_commercial_artboard
    result = generate(FakeService(), texts=texts or {'headline': {'text': '拿起新机，第一眼先看哪'}},
                      brief={'store_context': {'reader_task': '观察外观细节'}} if task else {})
    actual = httpx.AsyncClient
    requests = []
    def handler(request):
        requests.append(request)
        checks = [{'category': category, 'severity': 'info', 'message': '基于实际画面的具体观察'}
                  for category in ('text_reference', 'product_reference', 'publication', 'design')]
        if task:
            checks[-1].update(task_visible=True, task_location='画面右侧', task_observation='右侧局部放大与中央主体形成对应')
        payload = protocol_result(request, checks)
        if mutate:
            mutate(payload, len(requests))
        return httpx.Response(200, json={'choices': [{'message': {'content': json.dumps(payload)}}]})
    monkeypatch.setattr(httpx, 'AsyncClient', lambda **kw: actual(transport=httpx.MockTransport(handler), **kw))
    review = asyncio.run(review_commercial_artboard(result.artifact, base_url='https://vision.example/v1', key='secret',
        model='vision', assets=assets(), provenance=result.provenance, max_protocol_repairs=repairs))
    return review, result, requests


@pytest.mark.parametrize('placeholder', ['missing_task', 'unknown', 'N/A', '待补充'])
def test_v2_task_placeholder_never_becomes_observation(monkeypatch, placeholder):
    def mutate(payload, call):
        payload['checks'][-1]['task_observation'] = placeholder
    review, _, _ = run_protocol_review(monkeypatch, mutate, task=True)
    assert review['review_reason'] == 'placeholder_task_observation'
    assert not review['review_completed'] and not review['can_publish']
    assert review['retryable'] and review['recovery_action'] == 'review_same_artifact'


def test_v2_missing_task_location_is_recoverable_not_a_pass(monkeypatch):
    def mutate(payload, call):
        del payload['checks'][-1]['task_location']
    review, _, _ = run_protocol_review(monkeypatch, mutate, task=True)
    assert review['review_reason'] == 'missing_task_location'
    assert 'design.task_location' in review['missing_fields']


@pytest.mark.parametrize('row_status', ['match', 'content_mismatch'])
def test_v2_identical_expected_observed_cannot_claim_content_error(monkeypatch, row_status):
    def mutate(payload, call):
        text = payload['checks'][0]
        text['severity'] = 'error'
        text['message'] = '实际“拿起新机，第一眼先看哪”与要求“拿起新机，第一眼先看哪”不同'
        text['text_observations'][0]['status'] = row_status
    review, _, _ = run_protocol_review(monkeypatch, mutate)
    assert review['review_reason'] == 'contradictory_text_observation'
    assert not review['review_completed'] and not review['can_publish']
    assert review['contradictions']


def test_v2_mismatch_that_really_loses_content_is_a_valid_blocking_review(monkeypatch):
    def mutate(payload, call):
        text = payload['checks'][0]
        text['severity'] = 'error'
        row = text['text_observations'][0]
        row.update(observed='拿起机，第一眼先看哪', status='content_mismatch', location='左上主标题')
    review, _, requests = run_protocol_review(monkeypatch, mutate, repairs=1)
    assert review['review_completed'] and review['has_errors']
    assert not review['can_publish'] and len(requests) == 1  # actual content error is not schema repair


def test_v2_clear_line_break_in_place_of_editorial_comma_is_nonblocking_warning(monkeypatch):
    def mutate(payload, call):
        text = payload['checks'][0]
        text.update(severity='warning', message='上方标题以两行代替中间逗号，字词完整')
        text['text_observations'][0].update(observed='拿起新机\n第一眼先看哪', status='layout_only', location='上方两行标题')
    review, _, _ = run_protocol_review(monkeypatch, mutate)
    assert review['review_completed'] and review['can_publish']
    assert review['checks'][0]['severity'] == 'warning'
    assert review['checks'][0]['issue_code'] == 'layout_punctuation'
    assert not review['critical_uncertainties']


@pytest.mark.parametrize('text', ['价格1,299元', '支持功能A，仅限指定配置', '测试数据20小时，仅在实验室条件'])
def test_v2_prices_numbers_and_qualifiers_are_not_relaxed_as_layout_punctuation(monkeypatch, text):
    def mutate(payload, call):
        row = payload['checks'][0]['text_observations'][0]
        row.update(observed=text.replace(',', '\n').replace('，', '\n'), status='layout_only')
    review, _, _ = run_protocol_review(monkeypatch, mutate, texts={'headline': {'text': text, 'fact_ids': ['f1']}})
    assert review['review_reason'] == 'contradictory_text_observation'
    assert not review['can_publish']


def test_v2_protocol_repair_uses_same_images_once_and_records_both_attempts(monkeypatch):
    import json
    import hashlib
    from app.design_art_direction import DESIGN_REVIEW_PROTOCOL_VERSION
    def mutate(payload, call):
        if call == 1:
            payload['checks'][-1]['task_observation'] = 'missing_task'
    review, result, requests = run_protocol_review(monkeypatch, mutate, task=True, repairs=1)
    assert review['review_completed'] and len(requests) == 2
    first, second = [json.loads(request.content) for request in requests]
    assert first['messages'][1]['content'][1:] == second['messages'][1]['content'][1:]
    feedback = json.loads(second['messages'][1]['content'][0]['text'])['protocol_feedback']
    assert feedback['review_reason'] == 'placeholder_task_observation'
    assert [row['review_reason'] for row in review['review_attempts']] == ['placeholder_task_observation', 'completed']
    assert review['protocol_version'] == DESIGN_REVIEW_PROTOCOL_VERSION
    assert review['reviewed_artifact_sha256'] == hashlib.sha256(result.artifact.data).hexdigest()


def test_v2_second_invalid_protocol_stops_after_one_repair(monkeypatch):
    def mutate(payload, call):
        payload['checks'][-1]['task_observation'] = 'missing_task'
    review, _, requests = run_protocol_review(monkeypatch, mutate, task=True, repairs=1)
    assert len(requests) == 2 and len(review['review_attempts']) == 2
    assert not review['review_completed'] and not review['can_publish']


def test_v2_visible_task_and_missing_task_code_are_contradictory(monkeypatch):
    def mutate(payload, call):
        payload['checks'][-1]['issue_code'] = 'missing_task'
    review, _, _ = run_protocol_review(monkeypatch, mutate, task=True)
    assert review['review_reason'] == 'contradictory_task_observation'
    assert not review['can_publish']


def test_v2_compatibility_width_and_explicit_whitespace_are_layout_not_missing_content(monkeypatch):
    def mutate(payload, call):
        text = payload['checks'][0]
        text.update(severity='warning', message='空白呈现不同')
        text['text_observations'][0].update(observed='ＨＵＡＷＥＩ\nMate90', status='layout_only')
    review, _, _ = run_protocol_review(monkeypatch, mutate, texts={'identity-product-label': {'text': 'HUAWEI Mate90'}})
    assert review['review_completed'] and review['can_publish']
    assert review['checks'][0]['nonblocking_layout']


def test_specific_action_review_checks_contact_direction_and_occlusion_not_any_hand(monkeypatch):
    import json
    import httpx
    from app.design_art_direction import review_commercial_artboard
    client = FakeService()
    result = generate(client, brief={'store_context': {'reader_task': '观察手与机身的接触关系'}},
                      texts={'headline': {'text': '把机背贴掌心试试'}})
    assert '核对谁接触谁、哪一面朝哪里、哪些部位应被遮挡' in client.calls[0][0]
    actual = httpx.AsyncClient
    def handler(request):
        body = json.loads(request.content)
        assert '只出现手或托住另一面不算所述接触动作完成' in body['messages'][0]['content']
        checks = [{'category': category, 'severity': 'info', 'message': '实际像素观察'}
                  for category in ('text_reference', 'product_reference', 'publication', 'design')]
        checks[-1].update(task_visible=False, task_location='中部产品与手掌',
            task_observation='机背完整朝向观众，手掌在另一面托边，未接触文案要求的机背')
        return httpx.Response(200, json={'choices': [{'message': {'content': json.dumps(protocol_result(request, checks))}}]})
    monkeypatch.setattr(httpx, 'AsyncClient', lambda **kw: actual(transport=httpx.MockTransport(handler), **kw))
    review = asyncio.run(review_commercial_artboard(result.artifact, base_url='https://vision.example/v1', key='secret',
        model='vision', assets=assets(), provenance=result.provenance))
    assert review['protocol_version'] == DESIGN_REVIEW_PROTOCOL_VERSION
    assert review['has_errors'] and not review['can_publish']
    assert review['checks'][-1]['issue_code'] == 'missing_task'


@pytest.mark.parametrize('required,scope,status,observed,expected_error', [
    (False,'product_mark','content_mismatch','HUAWEI',False),
    (False,'not_found','missing','',False),
    (False,'overlay','content_mismatch','HUAWEI Mate 70',True),
    (True,'not_found','missing','',True),
])
def test_identity_overlay_preference_never_invents_text_or_hides_wrong_models(required,scope,status,observed,expected_error):
    from app.design_art_direction import _validate_review_text
    allowed={'source_text_id':'identity-product-label','role':'product_identity',
             'text':'HUAWEI Mate 80','required':required,'scope':'standalone_overlay'}
    check={'severity':'error','message':'fixture','text_observations':[{
        'source_text_id':'identity-product-label','expected':'HUAWEI Mate 80',
        'observed':observed,'location':'机身或排版对应位置','status':status,'observation_scope':scope}],
        'unexpected_text':[]}
    assert _validate_review_text(check,{'display_copy':[allowed]}) is None
    assert (check['severity']=='error') is expected_error
    assert check['text_observations'][0]['observed']==observed


@pytest.mark.parametrize('required', [False, True])
def test_omitted_identity_observation_is_unassessed_only_when_overlay_is_optional(required):
    from copy import deepcopy
    from app.design_art_direction import _validate_review_text
    observed = {'source_text_id': 'headline', 'expected': '沿边框看一个细节',
        'observed': '沿边框看一个细节', 'location': '下方标题', 'status': 'match'}
    check = {'severity': 'info', 'message': '只观察到标题',
             'text_observations': [deepcopy(observed)], 'unexpected_text': []}
    provenance = {'display_copy': [
        {'source_text_id': 'headline', 'role': 'headline', 'text': observed['expected']},
        {'source_text_id': 'identity-product-label', 'role': 'product_identity',
         'text': 'HUAWEI Mate 90', 'required': required, 'scope': 'standalone_overlay'}]}
    error = _validate_review_text(check, provenance)
    # A missing response is never converted into fabricated OCR, even on pass.
    assert check['text_observations'] == [observed]
    if required:
        assert error['reason'] == 'incomplete_text_observations'
        assert any('identity-product-label' in field for field in error['missing_fields'])
        assert 'unassessed_optional_copy_ids' not in check
    else:
        assert error is None and check['severity'] == 'warning'
        assert check['unassessed_optional_copy_ids'] == ['identity-product-label']
        assert check['issue_code'] == 'optional_identity_not_assessed'
        assert '未判断' in check['message']


@pytest.mark.parametrize('defect_type,expected_error', [('editorial_fit',False),('depicted_promise_contradiction',True)])
def test_editorial_fit_and_depicted_promise_have_distinct_delivery_effects(monkeypatch,defect_type,expected_error):
    def mutate(payload, call):
        payload['checks'][-1].update(task_visible=False, task_location='中央主视觉',
            task_observation='实际画面与所述单页任务存在需要说明的差异',defect_type=defect_type)
    review, _, _ = run_protocol_review(monkeypatch,mutate,task=True)
    assert review['review_completed'] is True
    assert review['has_errors'] is expected_error
