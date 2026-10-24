"""Bounded image execution controls; mock reviews make no quality claims."""
import asyncio
from copy import deepcopy
import hashlib
import json

import pytest
import httpx

from app import design_service as service
from app.design_review import cached_design_renderer, refine_design
from test_design_review import doc, render, visual


def test_explicit_visual_reviewer_takes_priority_over_art_director(monkeypatch):
    monkeypatch.setattr(service.settings, 'design_text_model', 'gpt-6-astra')
    monkeypatch.setattr(service.settings, 'image_review_model', 'gpt-4o')
    assert service._design_review_model() == 'gpt-4o'
    monkeypatch.setattr(service.settings, 'image_review_model', '')
    assert service._design_review_model() == 'gpt-6-astra'
    monkeypatch.setattr(service.settings, 'design_text_model', 'arbitrary-text-only-model')
    assert service._design_review_model() == ''


def test_final_context_keeps_cited_fact_beyond_twelve_without_cutting_evidence():
    facts = [{'id': f'f{index}', 'value': f'unrelated specification {index}'} for index in range(20)]
    keyboard = {'id': 'keyboard', 'value': '全尺寸键盘，支持独立数字键盘。' + '完整原文' * 500,
                'quote': '官方键盘规格原文' * 500, 'qualifiers': ['仅适用明确配置' * 300]}
    source = {'title': '试试输入一列数字', 'facts': facts + [keyboard],
        'blocks': [{'text': '全尺寸键盘，支持独立数字键盘。', 'fact_ids': ['keyboard'],
                    'disclosures': ['仅适用明确配置']}],
        'strategy': {'product_claim_review': {'claims': [{'claim_id': 'C1'}]}}}
    result = service._visual_marketing_context(source, {'platform': 'xiaohongshu'}, {}, {})
    assert result['product_facts'] == [keyboard]
    assert result['published_copy']['blocks'][0]['disclosures'] == ['仅适用明确配置']
    assert result['validated_product_claim_review'] == source['strategy']['product_claim_review']


def test_renderer_reuses_only_identical_painted_inputs():
    calls = []
    async def actual(document):
        calls.append(document.version)
        return render(document)
    cached = cached_design_renderer(actual)
    document = doc()
    async def run():
        first = await cached(document)
        await cached(document)
        annotated = document.model_copy(deep=True)
        annotated.version += 1
        annotated.audit.append({'type': 'synthetic_history'})
        assert await cached(annotated) == first
        annotated.layers[0].x += 1
        await cached(annotated)
    asyncio.run(run())
    assert len(calls) == 2


def test_noop_layer_suggestion_does_not_render_or_review_same_pixels_again():
    document = doc()
    title = next(layer for layer in document.layers if layer.role == 'headline')
    renders, reviews = [], []
    async def actual(current):
        renders.append(current.version)
        return render(current)
    async def reviewer(image, current):
        reviews.append(current.version)
        return visual(70, [{'id': title.id, 'changes': {'font_family': title.font_family}}])
    result = asyncio.run(refine_design(document, actual, reviewer=reviewer))
    assert renders == reviews == [1]
    assert result['iterations'] == 0 and result['review']['review_completed'] is True


@pytest.mark.parametrize('same_surface', [True, False])
def test_final_pixel_review_only_receives_matching_completed_text_claims(monkeypatch, same_surface):
    from app import design_review
    from test_design_marketing_visual import review_payload
    document = doc()
    png = render(document)
    public = '本次实际公开的产品文字'
    claim = {'claim_id': 'C1', 'surface': 'body:0', 'quote': public if same_surface else '旧稿已经改掉的文字',
             'verdict': 'supported'}
    captured = []
    validations = []
    original = httpx.AsyncClient
    def handler(request):
        metadata = json.loads(json.loads(request.content)['messages'][1]['content'][0]['text'])
        captured.append(metadata)
        result = visual()
        result['checks'] = [{'category': category, 'severity': 'info', 'message': '合成路由观察，不代表视觉验收'}
                            for category in ('composition', 'typography', 'imagery', 'platform', 'specificity')]
        result['marketing_review'] = review_payload()
        return httpx.Response(200, json={'choices': [{'message': {'content': json.dumps(result)}}]})
    monkeypatch.setattr(httpx, 'AsyncClient', lambda **kwargs:
        original(transport=httpx.MockTransport(handler), **kwargs))
    def inspect(raw, actual_png, **kwargs):
        validations.append(kwargs)
        assert actual_png == png
        return {'review_completed': True, 'action': 'deliver',
                'render_sha256': hashlib.sha256(actual_png).hexdigest()}
    monkeypatch.setattr(design_review, 'validate_marketing_visual_review', inspect)
    result = asyncio.run(design_review.review_with_model(png, document, 'https://synthetic.example/v1',
        'synthetic', 'gpt-4o', design_context={'marketing_context': {
            'published_copy': {'blocks': [{'text': public}]},
            'validated_product_claim_review': {'claims': [claim]}}}))
    assert result['review_completed']
    assert ('validated_product_claim_review' in validations[0]) == same_surface
    assert ('validated_product_claim_review' in captured[0]['context']) == same_surface


def test_final_claim_reconciliation_does_not_hide_unmatched_new_claim_findings():
    from app.design_review import validate_marketing_visual_review
    from test_design_marketing_visual import review_payload
    raw = review_payload()
    raw['dimensions']['product'] = {'status': 'revise', 'rationale': '新公开命题缺少依据',
        'findings': [{'quote': '新效果承诺', 'region': 'layer:new', 'reason': '无测试依据', 'action': '删除未支持承诺'}]}
    raw['revision_brief'] = '只处理新承诺'
    result = validate_marketing_visual_review(raw, render(doc()), public_text='新效果承诺',
        public_surfaces={'layer:new': '新效果承诺'},
        validated_product_claim_review={'claims': [{'claim_id': 'C1', 'surface': 'body:0', 'verdict': 'supported'}]})
    assert result['review_completed'] and result['action'] == 'revise'
    assert result['dimensions']['product']['findings'][0]['quote'] == '新效果承诺'

