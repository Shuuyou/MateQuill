import asyncio
from copy import deepcopy

from app.models import AuditItem, EvidenceBundle, TaskInput
from app.poster_copy import create_poster_copy


def bundle():
    return EvidenceBundle.model_validate({'product': 'TestBook', 'verified_live': True,
        'facts': [{'id': 'f1', 'product': 'TestBook', 'label': '内存', 'value': '16GB',
                  'quote': '内存16GB，需兼容设备', 'qualifiers': ['需兼容设备'], 'source_id': 's1'}],
        'sources': [{'id': 's1', 'title': '官方原文', 'excerpt': '内存16GB，需兼容设备',
                     'url': 'https://private.example/signed?secret=yes'}],
        'assets': [{'id': 'scene', 'product': 'TestBook', 'url': '', 'source_id': 's1', 'visual_role': 'scene'},
                   {'id': 'cover', 'product': 'TestBook', 'source_id': 's1', 'visual_role': 'product_overview',
                    'verified': True, 'url': 'https://private.example/image', 'local_path': '/private/file'},
                   {'id': 'pixel', 'product': 'TestBook', 'url': '', 'source_id': 's1', 'visual_role': 'product_overview',
                    'semantic_basis': 'pixel_review_sha256', 'verified': True}]})


class Provider:
    def __init__(self, evidence):
        self.evidence = evidence
    async def retrieve(self, product, query):
        return self.evidence


class Model:
    enabled = True
    model = 'synthetic'
    def __init__(self, omit=False, wrong=False):
        self.omit, self.wrong, self.calls = omit, wrong, 0
    async def _json(self, system, payload, **kwargs):
        self.calls += 1
        assert '单屏' in system
        assert 'sources' in payload['evidence']
        return {'title': '把想法写成下一页', 'intro': '', 'blocks': [
            {'text': '内存32GB' if self.wrong else '内存16GB', 'fact_ids': ['f1'],
             'disclosures': [] if self.omit and self.calls == 1 else ['需兼容设备']}], 'cta': '预约到店体验'}
    async def verify(self, result, evidence):
        self.calls += 1
        return [AuditItem(severity='info', category='semantic', message='所有产品主张均被引用直接支持')]


def run(model=None, evidence=None, **fields):
    task = TaskInput(**{'product': 'TestBook', 'audience': '学生', 'scene': '论文写作', 'platform': 'poster', 'mode': 'live', **fields})
    return asyncio.run(create_poster_copy(task, Provider(evidence or bundle()), model))


def test_real_single_screen_short_copy_and_safe_source_boundary():
    model = Model()
    result = run(model)
    assert result.status == 'ready', result.audit
    assert result.creative_plan == {}
    assert len(result.blocks) == 1
    assert [p.asset_id for p in result.images] == ['pixel']
    assert result.sources[0].excerpt == '内存16GB，需兼容设备'
    assert result.sources[0].url == ''
    assert all(a.local_path == '' and a.url.startswith('/api/assets/') for a in result.assets)
    assert model.calls == 2


def test_conditions_are_restored_from_evidence_before_independent_review():
    model = Model(omit=True)
    result = run(model)
    assert result.status == 'ready', result.audit
    assert model.calls == 2
    assert result.blocks[0].disclosures == ['需兼容设备']


def test_hallucinated_number_cannot_be_passed_by_semantic_info():
    model = Model(wrong=True)
    result = run(model)
    assert result.status == 'blocked'
    assert any(a.category == 'number' for a in result.audit)
    assert model.calls == 4


def test_without_model_is_explicit_draft_not_formal_generation():
    result = run()
    assert result.status == 'draft'
    assert any(a.category == 'model' for a in result.audit)
    assert result.blocks[0].disclosures == ['需兼容设备']


def test_scene_only_assets_are_never_used_as_product_overview():
    evidence = bundle()
    evidence.assets = evidence.assets[:1]
    result = run(Model(), evidence)
    assert result.status == 'blocked' and not result.images


def test_missing_input_needs_clarification():
    assert run(Model(), scene='').status == 'needs_input'


def test_conflicting_or_missing_facts_stop_before_model_call():
    for change in ({'facts': []}, {'conflicts': ['型号冲突']}, {'product': 'Other'}):
        evidence = bundle().model_copy(update=deepcopy(change))
        model = Model()
        assert run(model, evidence).status == 'blocked'
        assert model.calls == 0


def test_semantic_warning_without_explicit_support_blocks():
    class Uncertain(Model):
        async def verify(self, result, evidence):
            self.calls += 1
            return [AuditItem(severity='warning', category='semantic', message='尚未确定')]
    assert run(Uncertain()).status == 'blocked'
