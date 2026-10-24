"""朋友圈集成边界：合成资料/模型替身，不读取好友、不调用付费接口。"""
import asyncio
import copy

import pytest

from app.intake import resolve_intake
from app.models import AuditItem, EvidenceBundle, TaskInput
from app.store_campaigns import CampaignInput, plan_campaign
from app.workflow import AgentWorkflow


PRODUCT = 'HUAWEI TestBook Pro'
CATALOG = [{'id': 'testbook', 'name': PRODUCT, 'system': 'HarmonyOS'}]


def evidence():
    return EvidenceBundle.model_validate({
        'product': PRODUCT, 'system': 'HarmonyOS', 'verified_live': True,
        'facts': [{'id': 'f1', 'product': PRODUCT, 'label': '内存',
                   'value': '16GB', 'quote': '内存16GB。', 'source_id': 's1',
                   'system': 'HarmonyOS'}],
        'sources': [{'id': 's1', 'title': '合成产品资料',
                     'url': 'https://consumer.huawei.com/test', 'excerpt': '内存16GB。'}],
        'assets': [{'id': 'a1', 'product': PRODUCT, 'source_id': 's1',
                    'url': '/api/assets/a1', 'verified': True,
                    'title': '产品外观', 'visual_role': 'product_overview'}],
    })


def content():
    return {'title': '今天把桌面留给一台电脑', 'intro': '',
            'blocks': [{'text': '内存16GB。', 'fact_ids': ['f1']}],
            'cta': '想看哪个细节，可以告诉我。', 'tags': [],
            'images': [{'asset_id': 'a1', 'caption': '产品外观',
                        'position': '封面', 'role': 'cover', 'reason': '展示产品外观'}],
            'creative_plan': {'reader_tension': '想了解这台电脑的细节',
                              'reader_gain': '看一个真实产品信息',
                              'cards': [{'id': 'c1', 'headline': '今天的桌面',
                                         'body': '', 'asset_ids': ['a1'],
                                         'truth_status': 'official_material',
                                         'visual_intent': '让产品成为画面主体'}]}}


class Provider:
    async def retrieve(self, product, query):
        return evidence()


class Model:
    enabled = True
    supports_creative_plan = True
    model = 'synthetic-moments-test'

    def __init__(self, draft=None):
        self.draft = draft or content()
        self.seen_strategy = None

    async def generate(self, task, bundle, strategy):
        self.seen_strategy = copy.deepcopy(strategy)
        return copy.deepcopy(self.draft)

    async def verify(self, result, bundle):
        return [AuditItem(severity='info', category='semantic', message='事实均有直接支持，核验通过')]


def run(task=None, model=None):
    task = task or TaskInput(product=PRODUCT, audience='关注本店的朋友',
                             scene='展示今天的桌面', platform='moments', mode='live')
    return asyncio.run(AgentWorkflow(Provider(), model or Model()).run(task))


def intake(query, answers=None):
    return asyncio.run(resolve_intake(query, CATALOG, answers=answers))


def test_moments_stage_is_unknown_without_explicit_evidence():
    assert TaskInput(platform='moments').stage == 'unknown'


@pytest.mark.parametrize('source', ['进店领礼', '关注公众号', '参加社群活动', '附近有一所大学', '路过来试机'])
def test_intake_source_does_not_turn_into_purchase_intent_or_demand_form(source):
    result = intake(f'给本店发一条朋友圈，{source}', {'product': 'testbook'})
    assert result['status'] == 'ready'
    assert result['questions'] == []
    assert result['task']['stage'] == 'unknown'
    assert result['task']['audience'] == '关注本店的朋友'


def test_intake_keeps_explicit_audience_and_purchase_stage():
    result = intake('发一条朋友圈', {'product': 'testbook', 'audience': '已经买过电脑的顾客',
                                     'stage': 'post_purchase'})
    assert result['task']['audience'] == '已经买过电脑的顾客'
    assert result['task']['stage'] == 'post_purchase'


def test_other_platform_still_asks_for_missing_audience():
    result = intake('写一篇公众号', {'product': 'testbook'})
    assert result['status'] == 'clarify'
    assert [question['field'] for question in result['questions']] == ['audience']


def test_moments_strategy_reaches_actual_generator():
    model = Model()
    result = run(model=model)
    assert result.status == 'ready'
    assert result.strategy['stage'] == 'unknown'
    assert isinstance(model.seen_strategy['moments_strategy'], dict)
    assert model.seen_strategy['moments_strategy'] == result.strategy['moments_strategy']
    assert result.tags == []
    assert result.blocks[0].fact_ids == ['f1']
    assert result.creative_plan['platform'] == 'moments'


def test_unsupported_number_still_fails_after_moments_adaptation():
    draft = content()
    draft['blocks'][0]['text'] = '内存32GB。'
    result = run(model=Model(draft))
    assert result.status == 'blocked'
    assert any(item.category == 'number' and item.severity == 'error' for item in result.audit)


def test_official_product_image_cannot_be_relabeled_as_store_photo():
    draft = content()
    draft['creative_plan']['cards'][0]['truth_status'] = 'real_photo'
    result = run(model=Model(draft))
    assert result.status == 'blocked'
    assert any(item.category == 'creative_photo_truth' for item in result.audit)


def test_store_variants_do_not_turn_location_or_source_into_stage():
    stores = [{'id': 'a', 'name': '校园店', 'audience': '关注本店的朋友',
               'core_tasks': ['整理课程资料'], 'voice': '自然', 'location': '大学旁',
               'materials': ['进店领礼咨询']},
              {'id': 'b', 'name': '商务店', 'audience': '关注本店的朋友',
               'core_tasks': ['准备出差资料'], 'voice': '自然', 'location': '写字楼旁',
               'materials': ['公众号咨询']}]
    campaign = plan_campaign(CampaignInput(brief='分享今天店里的电脑', product=PRODUCT,
                                           platform='moments', store_ids=['a', 'b']), stores)
    left, right = campaign['variants']
    assert left['task']['stage'] == right['task']['stage'] == 'unknown'
    assert left['task']['requirements'] != right['task']['requirements']
    assert left['customer_task'] != right['customer_task']


@pytest.mark.parametrize('platform', ['xiaohongshu', 'wechat', 'poster'])
def test_other_platform_does_not_receive_moments_strategy(platform):
    model = Model()
    task = TaskInput(product=PRODUCT, audience='学生', scene='整理课程资料', platform=platform, mode='live')
    run(task, model)
    assert 'moments_strategy' not in model.seen_strategy
