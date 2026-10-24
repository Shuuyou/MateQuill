"""Offline integration of authorized store context, short copy and visual tools.

Image bytes and model decisions are fixtures. These tests prove evidence/context
plumbing and rejection boundaries, not visual quality or product fidelity.
"""
import asyncio
import base64
from copy import deepcopy
import io
import hashlib
import json
from types import SimpleNamespace
from uuid import uuid4

from PIL import Image
import pytest

from app import design_service as service
from app.design.planner import source_texts
from app.gtm_agent.place_context import build_place_context, place_requirements
from app.image_service import ImageArtifact
from app.models import TaskInput
from app.tenancy import save_body, load_body
from test_generation_jobs_durability import identities, acting

pytestmark = pytest.mark.real_tenancy
PRODUCT = 'HUAWEI Mate 90'
PRODUCT_URL = 'https://consumer.huawei.com/cn/phones/mate90/'


def png():
    buffer = io.BytesIO()
    Image.new('RGB', (80, 120), '#9EBA74').save(buffer, 'PNG')
    return buffer.getvalue()


def completed_fixture_review(raw, *, action=None):
    """An explicit offline verdict, not a measured commercial taste score."""
    review = {'errors': 0, 'warnings': 0, 'deterministic_completed': True,
        'review_completed': True, 'score': 90, 'checks': [
            {'category': category, 'severity': 'info', 'message': '离线流程夹具的完成审阅'}
            for category in ('composition', 'typography', 'imagery', 'platform', 'specificity')],
        'render_sha256': hashlib.sha256(raw).hexdigest()}
    if action:
        review.update(marketing_action=action,
            marketing_review={'review_completed': True, 'action': action,
                'render_sha256': hashlib.sha256(raw).hexdigest(), 'feedback': []})
    return review


def is_comparison_stage(kwargs):
    return getattr(kwargs.get('reviewer'), '__name__', '') == 'compare_reviewer'


class ScriptedModel:
    enabled = True
    def __init__(self, store_evidence, approved=True):
        self.store_evidence = store_evidence
        self.approved = approved
        self.calls = []

    async def _json(self, system, payload, **kwargs):
        self.calls.append((system, deepcopy(payload)))
        if 'schema' in payload and 'context' in payload:
            return {'cards': [{'id': 'local-cover', 'role': 'cover',
                'headline': '下班之前，收好灵感', 'caption': '看看这抹绿',
                'fact_ids': ['color-fact'], 'evidence_ids': [self.store_evidence]}]}
        if 'candidate' in payload and 'evidence' in payload:
            return {'checks': [{'id': 'local-cover', 'approved': self.approved,
                                'reason': '夹具独立审阅结果'}]}
        if 'brief' in payload:
            copy_id = 'design-copy-0-headline'
            if copy_id not in payload['brief']['approved_copy']:
                copy_id = 'title'
            return {'selected_concept_id': 'local-action', 'concepts': [{
                'id': 'local-action', 'name': '下班整理资料',
                'visual_thesis': '用随身物件与准确产品建立资料收尾任务，不用通用品牌悬浮图',
                'pages': [{'id': 'cover', 'role': 'cover', 'asset_id': 'real-product',
                    'text_ids': [copy_id],
                    'composition': [
                        {'role': 'headline', 'text_id': copy_id, 'x': .08, 'y': .06,
                         'width': .84, 'height': .16, 'font_scale': .045},
                        {'role': 'image', 'asset_id': 'real-product', 'image_kind': 'product',
                         'x': .20, 'y': .30, 'width': .60, 'height': .55}],
                    'visual_brief': '准确产品与笔记本共同构成一次下班整理动作，短标题表达任务，保留产品名。'}]}]}
        raise AssertionError('Unexpected model stage')


@pytest.fixture
def pipeline(identities, monkeypatch):
    from app import creative_agent_routes, design_art_direction, design_image_tools
    from app import design_brand_materials
    from app import design_review, image_service, store_task_research
    owner = identities[0]
    raw = png()
    asset = {'id': 'real-product', 'product': PRODUCT, 'verified': True,
        'provenance': 'huawei_official', 'source_id': 'official-product',
        'source_url': PRODUCT_URL, 'visual_role': 'product', 'view': 'rear',
        'title': '准确产品背面图', 'data_url': 'data:image/png;base64,' + base64.b64encode(raw).decode(),
        'sha256': hashlib.sha256(raw).hexdigest(), 'width': 80, 'height': 120}
    place = build_place_context([{'state': 'found', 'items': [{
        'source_id': 'nearby-office', 'url': 'https://www.example.com/office-location',
        'title': '园区公开位置资料', 'excerpt': '门店附近设有办公园区。'}]}], selection={
            'location_status': 'resolved', 'location_summary': '临近公开记录的办公园区',
            'audience': '这次写给下班前整理零散资料的人',
            'scene': '下班前把手机里的零散灵感整理起来',
            'visual_direction': '随身手机、笔记本和资料收尾动作构成画面',
            'evidence_ids': ['nearby-office']})
    with acting(owner):
        profile = load_body('store', owner['store']['id'])
        profile.update(name='园区里的华为门店', location='示例路20号',
                       service_capabilities=[{'name': '预约设备讲解', 'status': 'available'}])
        save_body('store', owner['store']['id'], profile)
        task = TaskInput(product=PRODUCT, mode='demo', store_id=owner['store']['id'],
                         requirements=place_requirements(place), scene='用户原先的泛化场景')
        run_id = uuid4().hex
        source = {'run_id': run_id, 'status': 'ready', 'title': '原先已审定的标题',
            'intro': '一条原先已审定的文字', 'blocks': [],
            'facts': [{'id': 'color-fact', 'product': PRODUCT, 'label': '配色', 'value': '晶格绿',
                       'quote': '晶格绿', 'qualifiers': [], 'source_id': 'official-product'}],
            'sources': [{'id': 'official-product', 'url': PRODUCT_URL, 'title': PRODUCT}],
            'images': [{'asset_id': 'real-product'}], 'assets': [asset],
            '_poster_task': task.model_dump(),
            # An upstream brand campaign is not this store's publishing identity.
            'campaign_context': {'publisher_kind': 'brand', 'publisher_identity': 'brand',
                                 'publisher_label': '华为终端'}}
        save_body('run', run_id, source)
    model = ScriptedModel('store:' + owner['store']['id'])
    captures = []
    class FakeImages:
        def __init__(self, *args, **kwargs):
            pass
        async def edit(self, prompt, image_bytes, *, reference_images):
            captures.append({'prompt': prompt, 'reference_images': reference_images})
            return ImageArtifact(raw, 'image/png', 'fixture-image-model', 'fixture-request', 80, 120, 1080, 1440)
    async def collect_assets(*args):
        return {'real-product': deepcopy(asset)}
    async def collect_brand_materials(*args):
        return {}, []
    async def observations(*args, **kwargs):
        return {'real-product': {'view': 'rear', 'framing': 'full_product', 'photo': False}}
    async def reference_review(artifact, *args, **kwargs):
        return {'protocol_version': design_art_direction.DESIGN_REVIEW_PROTOCOL_VERSION,
                'reviewed_artifact_sha256': hashlib.sha256(artifact.data).hexdigest(),
                'review_completed': True, 'has_errors': False, 'critical_uncertainties': [], 'checks': []}
    async def refine(document, renderer, **kwargs):
        rendered = await renderer(document)
        return {'document': document, 'review': completed_fixture_review(rendered,
            action=None if is_comparison_stage(kwargs) else 'deliver'),
            'reviews': [], 'png': rendered}
    async def offline_pixel_review(*args, **kwargs):
        return {'review_completed': True, 'score': 90,
                'checks': [{'category': 'fixture_review', 'severity': 'info',
                            'message': '离线审稿夹具，仅验证流程与上下文'}]}
    async def no_research(*args, **kwargs):
        pytest.fail('Must reuse the source-bound place envelope, without another search/model')
    monkeypatch.setattr(creative_agent_routes, 'model', lambda **kwargs: model)
    monkeypatch.setattr(service, 'collect_assets', collect_assets)
    # This plumbing fixture intentionally shares its scripted writer/auditor;
    # separate auditor routing is covered by design-copy protocol tests.
    monkeypatch.setattr(service, '_copy_auditor', lambda: None)
    monkeypatch.setattr(design_brand_materials, 'collect_brand_materials', collect_brand_materials)
    monkeypatch.setattr(image_service, 'ImageService', FakeImages)
    monkeypatch.setattr(design_image_tools, 'describe_design_assets', observations)
    monkeypatch.setattr(design_art_direction, 'review_commercial_artboard', reference_review)
    monkeypatch.setattr(design_review, 'refine_design', refine)
    monkeypatch.setattr(design_review, 'review_with_model', offline_pixel_review)
    monkeypatch.setattr(store_task_research, 'prepare_store_task', no_research)
    for name, value in {'image_api_key': 'fixture-no-real-secret',
        'image_base_url': 'https://image.example/v1', 'image_model': 'fixture-image-model',
        'image_review_model': 'fixture-review-model', 'llm_api_key': 'fixture-no-real-secret'}.items():
        monkeypatch.setattr(service.settings, name, value)
    return SimpleNamespace(owner=owner, task=task, source=source, run_id=run_id,
        model=model, captures=captures, asset=asset)


def execute(p):
    design_id = uuid4().hex
    with acting(p.owner):
        result = asyncio.run(service.execute_design({'design_id': design_id,
            'source_run_id': p.run_id, 'platform': 'xiaohongshu', 'instruction': ''}))
        response = json.loads(result.body)
        saved = load_body('design', response['id'])
        work = load_body('design_work', design_id)
    return saved, work


def test_approved_short_copy_and_store_brand_context_reach_real_plan_and_image_helper(pipeline):
    p = pipeline
    artifact, work = execute(p)
    brief = artifact['plan']['brief']
    assert work['place_research_status'] == 'reused'
    assert work['design_copy']['status'] == 'approved'
    assert work['design_copy']['audit']['completed'] is True
    short = brief['texts']['design-copy-0-headline']
    assert short['text'] == '下班之前，收好灵感'
    assert short['fact_ids'] == ['color-fact']
    assert short['source_ids'] == ['store:' + p.owner['store']['id']]
    assert short['audit_source_digest'] == work['design_copy']['source_digest']
    assert brief['store_context']['reader_task'] == '下班前把手机里的零散灵感整理起来'
    assert brief['store_context']['store']['id'] == p.owner['store']['id']
    assert brief['brand_identity']['product_naming']['canonical_name'] == PRODUCT
    assert brief['brand_identity']['product_naming']['source_ids'] == ['official-product']
    assert brief['publisher_kind'] == 'store'
    assert brief['brand_identity']['publisher']['role'] == 'store'
    assert brief['brand_identity']['publisher']['label'] == '园区里的华为门店'
    assert brief['brand_identity']['publisher']['official_brand_account_verified'] is False
    planner_call = next(payload for _, payload in p.model.calls if 'brief' in payload)
    assert planner_call['brand_identity'] == brief['brand_identity']
    assert planner_call['store_context'] == brief['store_context']
    assert planner_call['brief']['approved_copy']['design-copy-0-headline']['text'] == short['text']
    assert p.captures == []
    assert artifact['render_policy'] == 'asset_native.v3'
    board = artifact['artboards'][0]
    assert board['source_integrity']['source_bytes_unchanged'] is True
    product = next(layer for layer in board['document']['layers'] if layer['role'] == 'product')
    assert product['locked'] and product['asset_id'] == 'real-product'
    assert board['document']['assets']['real-product']['data_url'] == p.asset['data_url']
    assert board['document']['assets']['real-product']['sha256'] == p.asset['sha256']
    displayed_title = next(layer['text'] for layer in board['document']['layers'] if layer['role'] == 'headline')
    assert artifact['title'] == displayed_title == '下班之前，收好灵感'
    with acting(p.owner):
        assert load_body('run', p.run_id)['title'] == '原先已审定的标题'


def test_independently_rejected_short_copy_is_excluded_from_planner_and_delivery(pipeline):
    p = pipeline
    p.model.approved = False
    artifact, work = execute(p)
    assert work['design_copy']['status'] == 'rejected'
    assert not any(key.startswith('design-copy-') for key in artifact['plan']['brief']['texts'])
    planner_call = next(payload for _, payload in p.model.calls if 'brief' in payload)
    assert not any(key.startswith('design-copy-') for key in planner_call['brief']['approved_copy'])
    assert all('下班之前，收好灵感' not in capture['prompt'] for capture in p.captures)
    assert artifact['plan']['brief']['brand_identity']['publisher']['role'] == 'store'


@pytest.mark.parametrize('prepared', [
    {'status': 'rejected', 'audit': {'completed': True}},
    {'status': 'approved', 'audit': {'completed': False}},
    {'status': 'unavailable', 'audit': {'completed': True}},
])
def test_source_text_catalog_requires_completed_independent_short_copy_approval(prepared):
    record = {'title': '已审定旧标题', '_design_copy': {**prepared,
        'cards': [{'id': 'draft', 'role': 'cover', 'headline': '未经授权的新标题',
                   'fact_ids': ['f'], 'evidence_ids': ['store:s']}], 'source_digest': 'digest'},
        '_brand_identity': {'product_naming': {'canonical_name': PRODUCT, 'source_ids': ['official-product']}}}
    texts = source_texts(record)
    assert not any(key.startswith('design-copy-') for key in texts)
    assert texts['identity-product-label']['text'] == PRODUCT
    assert texts['identity-product-label']['source_ids'] == ['official-product']
    assert texts['title']['text'] == '已审定旧标题'

class RepairScriptedModel(ScriptedModel):
    """Write a distinct correction, then independently admit or reject it."""
    def __init__(self, store_evidence, reject_correction=False):
        super().__init__(store_evidence)
        self.copy_round = 0
        self.reject_correction = reject_correction

    async def _json(self, system, payload, **kwargs):
        result = await super()._json(system, payload, **kwargs)
        if 'schema' in payload and 'context' in payload:
            self.copy_round += 1
            if self.copy_round > 1:
                result['cards'][0]['headline'] = '先把今天的灵感收好'
        elif 'candidate' in payload and 'evidence' in payload and self.copy_round > 1:
            result['checks'][0]['approved'] = not self.reject_correction
            result['checks'][0]['reason'] = '独立检查修订文案，不接受创作者自证'
        return result


def install_final_repair_pipeline(pipeline, monkeypatch, *, second_action='deliver', reject_copy=False):
    from app import creative_agent_routes, design_review, image_service
    p = pipeline
    p.model = RepairScriptedModel('store:' + p.owner['store']['id'], reject_correction=reject_copy)
    monkeypatch.setattr(creative_agent_routes, 'model', lambda **kwargs: p.model)
    p.captures.clear()
    png_outputs, final_reviews = [], []
    class FullSizeImages:
        def __init__(self, *args, **kwargs):
            pass
        async def edit(self, prompt, image_bytes, *, reference_images):
            p.captures.append({'prompt': prompt, 'input_png': image_bytes, 'reference_images': reference_images})
            buffer = io.BytesIO()
            Image.new('RGB', (1080, 1440), '#9EBA74' if len(p.captures) == 1 else '#769E65').save(buffer, 'PNG')
            raw = buffer.getvalue()
            png_outputs.append(raw)
            return ImageArtifact(raw, 'image/png', 'fixture-full-size-model',
                                 f'fixture-full-size-{len(p.captures)}', 1080, 1440, 1080, 1440)
    async def final_review(document, renderer, **kwargs):
        if is_comparison_stage(kwargs):
            raw = await renderer(document)
            review = completed_fixture_review(raw)
            return {'document': document, 'review': review, 'reviews': [review], 'png': raw}
        final_reviews.append(document.model_copy(deep=True))
        action = 'revise' if len(final_reviews) == 1 else second_action
        visual = next(meta for meta in document.assets.values() if meta.get('data_url'))
        raw = await renderer(document)
        png_outputs.append(raw)
        review = {**completed_fixture_review(raw), 'marketing_action': action,
            'marketing_review': {'review_completed': True, 'action': action,
                'render_sha256': hashlib.sha256(raw).hexdigest(),
                'feedback': ['保留产品与配色，把读者的具体观察任务说清楚'] if action == 'revise' else []}}
        return {'document': document, 'review': review, 'reviews': [review], 'png': raw}
    monkeypatch.setattr(image_service, 'ImageService', FullSizeImages)
    monkeypatch.setattr(design_review, 'refine_design', final_review)
    return png_outputs, final_reviews


@pytest.mark.parametrize('second_action', ['deliver', 'revise'])
def test_final_marketing_revision_runs_one_audited_copy_and_targeted_image_correction(
        pipeline, monkeypatch, second_action):
    from fastapi import HTTPException
    p = pipeline
    png_outputs, final_reviews = install_final_repair_pipeline(p, monkeypatch, second_action=second_action)
    identifier = uuid4().hex
    parameters = {'design_id': identifier, 'source_run_id': p.run_id,
                  'platform': 'xiaohongshu', 'instruction': ''}
    with acting(p.owner):
        if second_action == 'deliver':
            response = asyncio.run(service.execute_design(parameters))
            artifact = load_body('design', json.loads(response.body)['id'])
            assert artifact['status'] == 'ready'
            assert artifact['plan']['brief']['texts']['design-copy-0-headline']['text'] == '先把今天的灵感收好'
            assert artifact['render_policy'] == 'asset_native.v3'
            assert artifact['artboards'][0]['source_integrity']['source_bytes_unchanged'] is True
        else:
            with pytest.raises(HTTPException) as blocked:
                asyncio.run(service.execute_design(parameters))
            assert blocked.value.status_code == 422
            assert blocked.value.detail['code'] == 'marketing_revise'
            with pytest.raises(HTTPException) as missing:
                load_body('design', identifier)
            assert missing.value.status_code == 404
        work = load_body('design_work', identifier)
        assert load_body('run', p.run_id)['title'] == p.source['title']
        assert load_body('run', p.run_id)['_poster_task'] == p.source['_poster_task']
    assert work['marketing_revision_count'] == 1
    assert p.captures == []
    assert len(final_reviews) == len(png_outputs) == 2
    assert p.model.copy_round == 2
    audits = [payload for _, payload in p.model.calls if 'candidate' in payload and 'evidence' in payload]
    assert len(audits) == 2
    assert audits[1]['candidate']['cards'][0]['headline'] == '先把今天的灵感收好'
    assert work['design_copy']['audit']['completed'] is True
    assert work['design_copy']['cards'][0]['headline'] == '先把今天的灵感收好'
    planner_calls = [payload for _, payload in p.model.calls if 'brief' in payload]
    assert len(planner_calls) == 2
    assert planner_calls[1]['brand_identity'] == planner_calls[0]['brand_identity']
    assert planner_calls[1]['store_context'] == planner_calls[0]['store_context']
    assert planner_calls[1]['revision_constraints']['locked_values']['background']
    assert '保持本次外观/体验选题与真实产品' in planner_calls[1]['brief']['instruction']
    assert png_outputs[0] != png_outputs[1]
    for document in final_reviews:
        assert document.assets['real-product']['data_url'] == p.asset['data_url']
        assert document.assets['real-product']['sha256'] == p.asset['sha256']


def test_final_marketing_revision_rejected_copy_never_purchases_second_image_or_commits_ready(pipeline, monkeypatch):
    from fastapi import HTTPException
    p = pipeline
    _, final_reviews = install_final_repair_pipeline(p, monkeypatch, reject_copy=True)
    identifier = uuid4().hex
    with acting(p.owner):
        with pytest.raises(HTTPException) as error:
            asyncio.run(service.execute_design({'design_id': identifier, 'source_run_id': p.run_id,
                'platform': 'xiaohongshu', 'instruction': ''}))
        assert error.value.status_code == 422
        assert error.value.detail['code'] == 'marketing_revise'
        work = load_body('design_work', identifier)
        with pytest.raises(HTTPException) as missing:
            load_body('design', identifier)
        assert missing.value.status_code == 404
    assert work['marketing_revision_count'] == 1
    assert work['status'] == 'marketing_revise'
    assert p.captures == []
    assert len(final_reviews) == 1
    # Initial copy plus one corrective copy and one feedback-guided copy rewrite.
    assert p.model.copy_round == 3
    audits = [payload for _, payload in p.model.calls if 'candidate' in payload and 'evidence' in payload]
    assert len(audits) == 3
    assert all(audit['candidate']['cards'][0]['headline'] == '先把今天的灵感收好' for audit in audits[1:])
    writers = [payload for _, payload in p.model.calls if 'schema' in payload and 'context' in payload]
    assert '独立文案审阅的具体意见' in writers[-1]['context']['design_instruction']
    assert len([payload for _, payload in p.model.calls if 'brief' in payload]) == 1
    assert work['design_copy']['cards'][0]['headline'] == '下班之前，收好灵感'
    assert all('先把今天的灵感收好' not in capture['prompt'] for capture in p.captures)


def test_publication_finding_requires_real_revision_even_when_marketing_delivers(pipeline, monkeypatch):
    from app import design_art_direction, design_review
    p = pipeline
    outputs, final_reviews = install_final_repair_pipeline(p, monkeypatch)
    async def reference_review(artifact, *args, **kwargs):
        first = len(p.captures) == 1
        return {'protocol_version': design_art_direction.DESIGN_REVIEW_PROTOCOL_VERSION,
            'reviewed_artifact_sha256': hashlib.sha256(artifact.data).hexdigest(),
            'review_completed': True, 'has_errors': first, 'critical_uncertainties': [],
            'checks': [{'category': 'publication', 'severity': 'error',
                        'message': '发布表达检查', 'task_location': '后方墙面',
                        'task_observation': '没有门店照片，删除虚构的门店墙面及体验台'}] if first else []}
    async def marketing_deliver(document, renderer, **kwargs):
        if is_comparison_stage(kwargs):
            raw = await renderer(document)
            review = completed_fixture_review(raw)
            return {'document': document, 'review': review, 'reviews': [review], 'png': raw}
        final_reviews.append(document.model_copy(deep=True))
        raw = await renderer(document)
        outputs.append(raw)
        if len(final_reviews) == 1:
            document.audit.append({'type': 'reference_comparison', 'checks': [{
                'category': 'publication', 'severity': 'error',
                'message': '后方墙面：没有门店照片，删除虚构的门店墙面及体验台'}]})
        review = {**completed_fixture_review(raw), 'marketing_action': 'deliver',
            'marketing_review': {'review_completed': True, 'action': 'deliver',
                                'render_sha256': hashlib.sha256(raw).hexdigest(), 'feedback': []}}
        return {'document': document, 'review': review, 'reviews': [review], 'png': raw}
    monkeypatch.setattr(design_art_direction, 'review_commercial_artboard', reference_review)
    monkeypatch.setattr(design_review, 'refine_design', marketing_deliver)
    artifact, work = execute(p)
    assert artifact['status'] == 'ready'
    assert work['marketing_revision_count'] == 1
    assert p.captures == []
    assert len(final_reviews) == 2
    assert '虚构的门店墙面' in work['marketing_feedback'][0]
    assert '后方墙面' in work['marketing_feedback'][0]
    assert work['design_copy']['cards'][0]['headline'] == '先把今天的灵感收好'
    # A corrected native composition, with the same authorized product bytes,
    # must receive a second independent verdict; changing status is insufficient.
    assert outputs[0] != outputs[1]
    assert all(doc.assets['real-product']['data_url'] == p.asset['data_url'] for doc in final_reviews)


@pytest.mark.parametrize('fixed_on_replan', [True, False])
def test_compile_overflow_replans_once_before_any_image_effect(pipeline, monkeypatch, fixed_on_replan):
    """Actual overflowing type is a compile defect, not a reason to buy a plate."""
    from app import design
    from app.design_review import audit_design_document
    from fastapi import HTTPException
    p = pipeline
    original_compose = design.compose_design
    original_treatment = service._design_elements_treatment
    compositions, element_entries = [], []
    def compose_with_overflow(*args, **kwargs):
        boards = original_compose(*args, **kwargs)
        if not compositions or not fixed_on_replan:
            title = next(layer for layer in boards[0].document.layers if layer.type == 'text')
            title.font_size = 3000
            assert any(check['category'] == 'overflow' and check['severity'] == 'error'
                       for check in audit_design_document(boards[0].document))
        compositions.append(boards[0].document.model_copy(deep=True))
        return boards
    async def only_compiled_elements(identifier, state, plan, board, assets):
        # If service calls this for an overflowing candidate, the test fails
        # before any provider can be constructed or charged.
        assert not any(check['severity'] == 'error' for check in audit_design_document(board.document))
        element_entries.append(board.id)
        return await original_treatment(identifier, state, plan, board, assets)
    monkeypatch.setattr(design, 'compose_design', compose_with_overflow)
    monkeypatch.setattr(service, '_design_elements_treatment', only_compiled_elements)
    identifier = uuid4().hex
    parameters = {'design_id': identifier, 'source_run_id': p.run_id,
                  'platform': 'xiaohongshu', 'instruction': ''}
    with acting(p.owner):
        if fixed_on_replan:
            response = asyncio.run(service.execute_design(parameters))
            artifact = load_body('design', json.loads(response.body)['id'])
            assert artifact['status'] == 'ready'
            assert element_entries
        else:
            with pytest.raises(HTTPException) as caught:
                asyncio.run(service.execute_design(parameters))
            assert caught.value.status_code == 422
            assert caught.value.detail['code'] == 'commercial_direction_needs_revision'
            assert element_entries == []
            with pytest.raises(HTTPException) as missing:
                load_body('design', identifier)
            assert missing.value.status_code == 404
        work = load_body('design_work', identifier)
        assert work['art_direction_replans'] == 1
        assert len(work['art_direction_history']) == 1
        assert any(check['category'] == 'overflow' for finding in work['art_direction_feedback']
                   for check in finding['checks'])
        assert load_body('run', p.run_id)['title'] == p.source['title']
    planner_calls = [payload for _, payload in p.model.calls if 'brief' in payload]
    assert len(planner_calls) == 2
    assert '不以缩小字或退回纯色模板换取通过' in planner_calls[1]['brief']['instruction']
    assert p.captures == []
