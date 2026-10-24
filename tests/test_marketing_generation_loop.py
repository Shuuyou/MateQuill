"""Synthetic end-to-end review decisions, no paid models or public searches."""
import asyncio
from copy import deepcopy
import json

from app.llm import OpenAICompatibleLLM
from app.models import AuditItem, EvidenceBundle, RunResult, TaskInput
from app.workflow import AgentWorkflow

PRODUCT = 'HUAWEI TestBook Pro'


def bundle():
    return EvidenceBundle.model_validate({'product': PRODUCT, 'system': 'HarmonyOS', 'verified_live': True,
        'facts': [{'id': 'f1', 'product': PRODUCT, 'label': '内存', 'value': '16GB',
                   'quote': '内存16GB。', 'source_id': 's1', 'system': 'HarmonyOS'}],
        'sources': [{'id': 's1', 'title': '合成资料', 'url': 'https://consumer.huawei.com/test', 'excerpt': '内存16GB。'}],
        'assets': [{'id': 'a1', 'product': PRODUCT, 'source_id': 's1', 'url': '/api/assets/a1',
                    'verified': True, 'title': '产品外观', 'visual_role': 'product_overview'}]})


def draft(title='把晚霞装进口袋'):
    return {'title': title, 'intro': '', 'blocks': [{'text': '内存16GB。', 'fact_ids': ['f1']}],
            'cta': '想看哪个细节，可以告诉我。', 'tags': [],
            'images': [{'asset_id': 'a1', 'caption': '产品外观', 'position': '封面', 'role': 'cover'}]}


def task():
    return TaskInput(product=PRODUCT, audience='关注本店的朋友', scene='街角散步', platform='moments', mode='live')


class Provider:
    async def retrieve(self, product, query):
        return bundle()


class ReviewedModel:
    enabled = True
    model = 'synthetic-editor'

    def __init__(self, decisions, initial_title='它的夜，比你的班亮'):
        self.decisions = list(decisions)
        self.initial_title = initial_title
        self.repairs = self.reselections = self.verifications = 0
        self.marketing_review_history = []
        self.seen_rejected = None

    async def generate(self, task, bundle, strategy):
        return draft(self.initial_title)

    async def verify(self, result, bundle):
        decision = self.decisions[min(self.verifications, len(self.decisions) - 1)]
        self.verifications += 1
        self.marketing_review = {'decision': decision, 'revision_brief': '换共同生活观察，保留具体钩子'}
        self.marketing_review_history.append(deepcopy(self.marketing_review))
        return [AuditItem(severity='info', category='semantic', message='所有事实均有直接支持，核验通过'),
                AuditItem(severity='info' if decision == 'accept' else 'error',
            category='marketing_empathy', message='通过' if decision == 'accept' else '具体关系问题')]

    async def reselect(self, task, bundle, strategy, result):
        self.reselections += 1
        self.seen_rejected = result.title
        return draft()

    async def repair(self, task, bundle, strategy, result):
        self.repairs += 1
        return draft(result.title)


def execute(model):
    return asyncio.run(AgentWorkflow(Provider(), model).run(task()))


def test_reselect_rewrites_instead_of_polishing_and_preserves_rejected_history():
    model = ReviewedModel(['reselect', 'accept'])
    result = execute(model)
    assert result.status == 'ready'
    assert model.reselections == 1 and model.repairs == 0
    assert model.seen_rejected == '它的夜，比你的班亮'
    assert result.title == '把晚霞装进口袋'
    assert result.strategy['marketing_review']['decision'] == 'accept'
    assert [row['decision'] for row in result.strategy['marketing_review_history']] == ['reselect', 'accept']
    history = result.strategy.get('marketing_rejected_drafts', [])
    assert '它的夜，比你的班亮' in json.dumps(history, ensure_ascii=False)
    assert any(t.stage == 'automatic_reselection' for t in result.trace)


def test_second_review_unresolved_stays_blocked():
    model = ReviewedModel(['reselect', 'revise'])
    result = execute(model)
    assert result.status == 'blocked'
    assert model.reselections == 1
    assert any(item.severity == 'error' and item.category == 'marketing_empathy' for item in result.audit)


def test_local_repair_keeps_existing_hook_without_forcing_reselection():
    model = ReviewedModel(['revise', 'accept'], initial_title='把晚霞装进口袋')
    result = execute(model)
    assert result.status == 'ready'
    assert result.title == '把晚霞装进口袋'
    assert model.repairs == 1 and model.reselections == 0


def test_legacy_fake_without_marketing_support_remains_compatible():
    class Legacy:
        enabled = True
        model = 'legacy'

        async def generate(self, task, bundle, strategy):
            return draft()

        async def verify(self, result, bundle):
            return [AuditItem(severity='info', category='semantic', message='事实均有直接支持，核验通过')]
    result = execute(Legacy())
    assert result.status == 'ready'
    assert 'marketing_review' not in result.strategy


def test_canonical_reselection_updates_text_and_image_scene_keeps_sources(monkeypatch):
    from app.gtm_agent.place_context import build_place_context, place_requirements, local_editorial_context
    from app import marketing_context

    source = {'source_id': 'p1', 'title': '公开街区资料', 'url': 'https://www.shanghai.gov.cn/test',
              'excerpt': '街区有公共步道。'}
    context = build_place_context([{'items': [source]}], selection={
        'location_status': 'resolved', 'location_summary': '临近公共步道', 'audience': '散步的朋友',
        'scene': '加班后疲惫换新', 'visual_direction': '疲惫上班者与新机对照', 'evidence_ids': ['p1']})
    value = task()
    value.requirements = '发一条朋友圈' + place_requirements(context)
    before = local_editorial_context(value)
    model = OpenAICompatibleLLM()
    model.marketing_context = {'local_editorial_context': before}
    model.marketing_review = {'decision': 'reselect', 'revision_brief': '换成步道晚霞的共同观察，不定义顾客处境'}
    captured = {}

    async def enrich(context, review):
        return deepcopy(context)

    async def generate(task, bundle, strategy):
        captured.update(strategy)
        return draft()

    monkeypatch.setattr(marketing_context, 'enrich_review_evidence', enrich)
    monkeypatch.setattr(model, 'generate', generate)
    result = RunResult.model_validate(draft('它的夜，比你的班亮'))
    asyncio.run(model.reselect(value, bundle(), {}, result))
    after = local_editorial_context(value)
    assert after['evidence'] == before['evidence']
    assert after['scene'] == value.scene
    assert before['location_summary'] in after['scene']
    assert '疲惫上班者与新机对照' not in after['visual_direction']
    assert captured['reselection_feedback'] == model.marketing_review['revision_brief']
    assert model.marketing_review['revision_brief'] not in after['scene']
    assert model.marketing_context['local_editorial_context'] == after
    assert captured['reselect'] is True and captured['repair'] is False
    assert captured['previous_draft']['title'] == '它的夜，比你的班亮'


def test_real_repair_contract_preserves_creative_and_source_bound_images(monkeypatch):
    model = OpenAICompatibleLLM()
    captured = {}

    async def generate(task, bundle, strategy):
        captured.update(strategy)
        value = draft()
        value['images'] = []
        return value

    monkeypatch.setattr(model, 'generate', generate)
    result = RunResult.model_validate(draft())
    result.audit = [AuditItem(severity='error', category='marketing_world', message='仅修错误地点')]
    output = asyncio.run(model.repair(task(), bundle(), {}, result))
    assert captured['repair'] is True
    assert captured['previous_draft']['title'] == '把晚霞装进口袋'
    assert '保留' in captured['repair_policy'] and '免责声明' in captured['repair_policy']
    assert output['title'] == '把晚霞装进口袋'
    assert output['images'][0]['asset_id'] == 'a1'
