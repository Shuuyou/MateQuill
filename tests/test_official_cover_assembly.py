"""End-to-end workflow assembly of a real, hash-reviewed synthetic cover."""
import asyncio
import base64
import hashlib
import io
import json

from fastapi.responses import Response
from PIL import Image
import pytest

from app import visual_assets
from app.creative_export import embed_card_assets, render_creative_html
from app.models import Asset, AuditItem, EvidenceBundle, Fact, Source, TaskInput
from app.workflow import AgentWorkflow


@pytest.fixture
def cover_setup(monkeypatch, tmp_path):
    stream = io.BytesIO()
    Image.new('RGB', (80, 60), '#abcabc').save(stream, format='PNG')
    raw = stream.getvalue()
    digest = hashlib.sha256(raw).hexdigest()
    path = tmp_path / 'cover.png'
    path.write_bytes(raw)
    registry = tmp_path / 'visual-reviews.json'
    registry.write_text(json.dumps({'reviews': {digest: {
        'observed_title': '产品总览画面', 'semantic_tags': ['overview'],
        'visual_role': 'product_overview', 'usable': True,
    }}}), encoding='utf-8')
    monkeypatch.setattr(visual_assets, 'REVIEW_PATH', registry)
    product = 'HUAWEI SyntheticBook'
    quote = '机身颜色：绿色'
    bundle = EvidenceBundle(product=product, verified_local=True,
        facts=[Fact(id='f1', product=product, label='机身颜色', value='绿色', quote=quote, source_id='s1')],
        sources=[Source(id='s1', title='合成官方资料', provider='huawei_official',
                        url='https://consumer.huawei.com/cn/syntheticbook/', excerpt=quote,
                        sha256=hashlib.sha256(quote.encode()).hexdigest())],
        assets=[Asset(id='official-cover', product=product, source_id='s1',
                      url='/api/assets/official-cover', provenance='huawei_official',
                      title='产品总览画面', verified=True, sha256=digest,
                      visual_role='product_overview', semantic_basis='pixel_review_sha256')])

    class Provider:
        async def retrieve(self, *args):
            return bundle
        async def get_asset(self, identifier):
            return path if identifier == 'official-cover' else None

    class Model:
        enabled = True
        model = 'synthetic-test-model'
        def __init__(self):
            self.reviewed_image_ids = []
        async def generate(self, *args):
            return {'title': '把任务准备好再出发', 'intro': '这次先整理自己的资料。',
                    'blocks': [{'text': '绿色机身，放进你的选色清单。', 'fact_ids': ['f1']}],
                    'images': [], 'cta': '聊聊你的使用习惯。'}
        async def verify(self, result, _bundle):
            self.reviewed_image_ids.append([image.asset_id for image in result.images])
            return [AuditItem(severity='info', category='semantic', message='所有事实直接支持，核验通过')]

    task = TaskInput(product=product, ecosystem_products=['HUAWEI SyntheticPhone'], audience='学生',
                     scene='整理课程资料', platform='xiaohongshu', mode='local')
    return bundle, Provider(), Model(), task, path, raw


def test_omitted_images_get_a_real_reviewed_cover_before_independent_verification(cover_setup):
    bundle, provider, model, task, _, _ = cover_setup
    result = asyncio.run(AgentWorkflow(provider, model).run(task))
    assert result.status == 'ready'
    assert model.reviewed_image_ids == [['official-cover']]
    image = result.images[0]
    assert image.role == 'cover' and image.block_index is None and image.supported_fact_ids == []
    assert image.caption == '官方产品资料图'
    assert any(trace.stage == 'cover_assembly' for trace in result.trace)
    assert not any(issue.severity == 'error' for issue in result.audit)


@pytest.mark.parametrize('mutation', ['wrong_model', 'missing_hash', 'wrong_hash', 'unreviewed',
                                     'missing_source', 'wrong_source', 'unverified', 'changed_bytes'])
def test_unqualified_material_does_not_fill_empty_model_images(cover_setup, mutation):
    bundle, provider, model, task, path, _ = cover_setup
    asset = bundle.assets[0]
    if mutation == 'wrong_model': asset.product = 'HUAWEI OtherBook'
    if mutation == 'missing_hash': asset.sha256 = ''
    if mutation == 'wrong_hash': asset.sha256 = '0' * 64
    if mutation == 'unreviewed': asset.semantic_basis = 'title_only'
    if mutation == 'missing_source': bundle.sources = []
    if mutation == 'wrong_source': bundle.sources[0].url = 'https://example.com/unreviewed'
    if mutation == 'unverified': asset.verified = False
    if mutation == 'changed_bytes': path.write_bytes(b'not-an-image')
    result = asyncio.run(AgentWorkflow(provider, model).run(task))
    assert result.status == 'blocked' and result.images == []
    assert not any(trace.stage == 'cover_assembly' for trace in result.trace)


def test_invalid_explicit_selection_is_not_replaced_to_hide_an_error(cover_setup):
    bundle, provider, model, task, _, _ = cover_setup
    original = model.generate
    async def wrong_selection(*args):
        draft = await original(*args)
        draft['images'] = [{'asset_id': 'not-in-bundle', 'caption': '产品资料图', 'position': '封面', 'role': 'cover'}]
        return draft
    model.generate = wrong_selection
    result = asyncio.run(AgentWorkflow(provider, model).run(task))
    assert result.status == 'blocked'
    assert not any(trace.stage == 'cover_assembly' for trace in result.trace)


def test_export_embeds_real_document_cover_even_without_a_storyboard_card(monkeypatch, cover_setup):
    from app import api
    bundle, _, _, _, _, raw = cover_setup
    async def asset(identifier):
        assert identifier == 'official-cover'
        return Response(raw, media_type='image/png')
    monkeypatch.setattr(api, 'asset', asset)
    record = {'status': 'ready', 'title': '实际成稿', 'assets': [bundle.assets[0].model_dump()],
              'images': [{'asset_id': 'official-cover', 'role': 'cover', 'caption': '官方产品资料图'}],
              'creative_plan': {'cards': []}, 'blocks': [{'text': '这是一段正文。'}]}
    embedded = asyncio.run(embed_card_assets(record))
    assert base64.b64decode(embedded['official-cover'].split(',', 1)[1]) == raw
    document = render_creative_html(record, embedded)
    assert embedded['official-cover'] in document and '官方产品资料图' in document
    assert 'document-picture' in document
    assert '<section class="card">' not in document
