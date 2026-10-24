"""Reader payoff contract and adversarial boundaries, no paid model calls."""
import asyncio
from copy import deepcopy

import pytest

from app.marketing_review import DIMENSIONS, review_marketing, review_audit_items, _preserve_review_judgements
from app.marketing_surfaces import marketing_public_surfaces
from app.reader_value import (build_reader_contract, validate_reader_value, reader_revision_brief, reader_intent)
from app.editorial import editorial_review
from app.models import TaskInput, RunResult, ContentBlock


TASK = {'product': 'HUAWEI TestBook', 'platform': 'xiaohongshu',
        'audience': '关注本店的朋友', 'scene': '用三步讲清楚照片传到电脑', 'goal': '给可照做的教程'}
DRAFT = {'title': '三步把照片带到电脑', 'intro': '',
         'blocks': [{'text': '内存16GB，欢迎到店看看。'}], 'cta': '来聊聊',
         'creative_plan': {'cards': [{'id': 'c1', 'headline': '三步传照片', 'body': '好照片，值得分享。'}]}}


def marketing():
    return {'schema_version': '1', 'phase': 'draft', 'dimensions': {
        key: {'status': 'pass', 'rationale': '有据且符合当前关系', 'findings': []} for key in DIMENSIONS},
        'creative_effectiveness': {'status': 'pass', 'hook': '具体问题', 'local_value': '实际任务',
            'brand_connection': '与产品相关', 'natural_action': '自然邀请', 'distinctiveness': '独立表达',
            'preserve': ['三步把照片带到电脑'], 'improvements': []}, 'revision_brief': ''}


def judgement(contract, *, gap=False):
    units = []
    for identifier, surfaces in contract['units'].items():
        region = next((s for s in surfaces if s.startswith('body:')), surfaces[-1])
        if identifier == 'card_copy':
            region = next(s for s in surfaces if s.endswith('.body'))
        findings = [{'region': region, 'quote': '', 'kind': 'unfulfilled_promise',
                     'reason': '标题说三步，实际只给规格与邀请，没有方法',
                     'target': '在已批准材料内交出操作方法，保留原来的具体问题',
                     'action': 'revise', 'repair_surfaces': [region]}] if gap else []
        units.append({'unit_id': identifier, 'status': 'met', 'promise': '交出具体阅读回报',
                      'payoff': '已按真实字段检查', 'first_screen': '进入当次问题',
                      'evidence': [{'region': region, 'quote': ''}], 'findings': findings})
    return {'contract_id': contract['contract_id'], 'intent': contract['intent'],
            'units': units, 'preserve': [{'region': 'title', 'quote': ''}], 'status': 'pass'}


class Client:
    supports_reader_value_contract = True
    max_calls = 5

    def __init__(self, gap=False):
        self.gap, self._calls, self.payload = gap, 0, None

    async def _json(self, system, payload, temperature):
        self._calls += 1
        self.payload = payload
        result = marketing()
        result['reader_value'] = judgement(payload['reader_value_contract'], gap=self.gap)
        return result


def test_tutorial_promise_gap_blocks_even_with_all_six_dimensions_passing_one_call():
    client = Client(gap=True)
    result = asyncio.run(review_marketing(client, task=TASK, draft=DRAFT, evidence={}))
    assert client._calls == 1
    assert result['decision'] == 'revise'
    assert result['reader_value']['status'] == 'revise'
    assert all(row['status'] == 'pass' for row in result['dimensions'].values())
    assert '三步把照片带到电脑' in result['revision_brief']
    assert 'body:0' in result['revision_brief']
    assert any(item.category == 'reader_value' and item.severity == 'error' for item in review_audit_items(result))
    assert client.payload['reader_value_contract']['intent'] == 'tutorial'
    assert 'reader_value' in client.payload['schema']['required']


@pytest.mark.parametrize('text,expected', [
    ('给设计学生一份选购取舍', 'buying_tradeoff'),
    ('写一个到店体验想法', 'local_experience'),
    ('不是教程，只想分享晚霞配色的心情', 'aesthetic_emotion'),
    ('分享今天街角的一点日常观察', 'everyday_observation'),
])
def test_intent_follows_actual_task_not_one_universal_tutorial(text, expected):
    assert reader_intent({'scene': text}) == expected


def test_selected_emotion_does_not_force_an_explanatory_checklist():
    task = {'platform': 'moments', 'scene': '校园的晚霞', 'goal': '让人看见颜色'}
    draft = {'title': '把晚霞装进口袋', 'blocks': [{'text': '街角那一点橙，刚好落在掌心。'}], 'cta': ''}
    contract = build_reader_contract(task, draft, selected_concept={'mechanism': 'emotional_scene', 'payoff': '颜色和一句心情'})
    assert contract['intent'] == 'aesthetic_emotion'
    judged = validate_reader_value(judgement(contract), contract, marketing_public_surfaces(draft))
    assert judged['status'] == 'pass'
    assert '强迫步骤' in contract['rubric']


def subject_draft(title, intro):
    return {'title': title, 'intro': intro,
            'blocks': [{'text': 'MateBook 的机身重量约 1.33kg。带上充电器，装进常用包，来店里试试实际背负感。'}],
            'cta': ''}


def subject_gap(contract):
    raw = judgement(contract)
    raw['units'][0]['findings'] = [{'region': 'title', 'quote': '', 'kind': 'subject_ambiguity',
        'reason': '标题的重量痛点没有明确主体，首屏直接转入所推产品，读者可能把负面体验归到当前营销对象',
        'target': '仅在标题自然区分原有设备的负担与本次试背对象；保留日常奔波的痛点与口语语气，不改写正文事实',
        'action': 'revise', 'repair_surfaces': ['title'],
        'related_evidence': [{'region': 'body:0', 'quote': ''}]}]
    raw['preserve'] = [{'region': 'body:0', 'quote': ''}]
    return raw


def test_ambiguous_weight_pain_gets_located_title_context_gap_in_existing_single_review():
    draft = subject_draft('一天换三个地方，电脑是真沉', '教室、图书馆、宿舍，一天来回走。')
    task = {'product': 'HUAWEI MateBook', 'platform': 'moments', 'scene': '日常背电脑奔波的观察', 'goal': '自然宣传'}
    class SubjectClient(Client):
        async def _json(self, system, payload, temperature):
            self._calls += 1
            self.payload = payload
            result = marketing()
            result['reader_value'] = subject_gap(payload['reader_value_contract'])
            return result
    client = SubjectClient()
    result = asyncio.run(review_marketing(client, task=task, draft=draft, evidence={}))
    assert client._calls == 1 and result['decision'] == 'revise'
    finding = result['reader_value']['units'][0]['findings'][0]
    assert finding['quote'] == draft['title']
    assert finding['related_evidence'][0]['quote'] == draft['blocks'][0]['text']
    assert finding['repair_surfaces'] == ['title']
    assert '保留日常奔波' in result['revision_brief'] and '1.33kg' in result['revision_brief']
    assert client.payload['reader_value_contract']['promoted_product'] == task['product']
    assert '不要求正向广告' in client.payload['reader_value_contract']['subject_alignment_standard']


@pytest.mark.parametrize('title,intro', [
    ('以前背包像搬家，今天换个轻一点的试试', '用了几年的那台旧电脑，每次换教室都要掂量一下；这次想试背的是 MateBook。'),
    ('电脑是真沉——说的是我用了五年的那台', '旧设备留在桌上，今天把 MateBook 装进同一个包试试。'),
    ('这台电脑也有我不喜欢的地方', '说的就是今天试用的 MateBook：重量之外，接口布局也想亲手看看。'),
    ('把黄昏背回去', '不是在抱怨哪台电脑，只是包里装着设备，眼前装着晚霞。'),
])
def test_clear_old_new_contrast_real_product_criticism_and_aesthetic_stay_allowed(title, intro):
    draft = subject_draft(title, intro)
    task = {'product': 'HUAWEI MateBook', 'scene': '生活观察和情绪表达'}
    contract = build_reader_contract(task, draft)
    raw = judgement(contract)
    raw['units'][0]['evidence'] = [{'region': 'intro', 'quote': ''}, {'region': 'body:0', 'quote': ''}]
    assessed = validate_reader_value(raw, contract, marketing_public_surfaces(draft))
    assert assessed['status'] == 'pass'
    assert '负面痛点' in contract['subject_alignment_standard']
    assert '不强制出现' in contract['subject_alignment_standard']


@pytest.mark.parametrize('mutation', ['missing_context', 'private_context', 'fake_context'])
def test_subject_gap_cannot_be_an_unlocated_generic_negative_word_filter(mutation):
    draft = subject_draft('换三个地方，包里的负担一直在', '今天想换个设备试试。')
    contract = build_reader_contract({'product': 'HUAWEI MateBook'}, draft)
    raw = subject_gap(contract)
    finding = raw['units'][0]['findings'][0]
    if mutation == 'missing_context': finding['related_evidence'] = []
    elif mutation == 'private_context': finding['related_evidence'] = [{'region': 'strategy', 'quote': ''}]
    else: finding['related_evidence'][0]['quote'] = '不存在的旧设备解释'
    with pytest.raises(ValueError): validate_reader_value(raw, contract, marketing_public_surfaces(draft))


def test_subject_clarity_repair_respects_title_only_scope_and_keeps_body():
    draft = subject_draft('换三个地方，电脑是真沉', '来回奔波的一天。')
    contract = build_reader_contract({'product': 'HUAWEI MateBook'}, draft,
        edit_scope={'allowed_surfaces': ['title'], 'instruction': '只改标题，正文和配图保持'})
    assessed = validate_reader_value(subject_gap(contract), contract, marketing_public_surfaces(draft))
    assert assessed['scope_conflicts'] == []
    brief = reader_revision_brief(assessed, contract)
    assert '本轮只可修改：title' in brief and 'body:0' in brief and '必须保留' in brief


def test_aesthetic_editorial_does_not_add_cta_or_force_three_paragraphs():
    for platform in ['xiaohongshu', 'wechat']:
        task = TaskInput(product='HUAWEI TestBook', audience='本店读者', scene='晚霞配色的心情', platform=platform)
        run = RunResult(run_id='aesthetic-test', mode='local', title='把晚霞装进口袋',
                        blocks=[ContentBlock(text='街角那一点橙，刚好落在掌心。')])
        assert not any(item.severity == 'warning' for item in editorial_review(task, run))


def test_title_and_cta_cannot_certify_a_tutorial_answer():
    draft = {'title': '三步传照片', 'blocks': [{'text': '欢迎到店'}], 'cta': '来聊聊'}
    contract = build_reader_contract(TASK, draft)
    raw = judgement(contract)
    raw['units'][0]['evidence'] = [{'region': 'title', 'quote': ''}]
    with pytest.raises(ValueError, match='not_reader_answer'):
        validate_reader_value(raw, contract, marketing_public_surfaces(draft))


@pytest.mark.parametrize('mutation', ['private', 'stale', 'unit_missing', 'fake_quote', 'imaginary_pixels'])
def test_reader_judge_cannot_approve_missing_or_unobserved_deliverables(mutation):
    contract = build_reader_contract(TASK, DRAFT)
    raw = judgement(contract)
    if mutation == 'private':
        raw['units'][0]['evidence'][0]['region'] = 'strategy.internal_plan'
    elif mutation == 'stale':
        raw['contract_id'] = '0' * 64
    elif mutation == 'unit_missing':
        raw['units'] = raw['units'][:1]
    elif mutation == 'fake_quote':
        raw['units'][0]['evidence'][0]['quote'] = '不存在的具体操作步骤'
    else:
        raw['units'][0]['evidence'][0]['region'] = '画面右上角'
    with pytest.raises(ValueError):
        validate_reader_value(raw, contract, marketing_public_surfaces(DRAFT))


def test_mutually_exclusive_titles_are_not_one_published_post():
    draft = deepcopy(DRAFT)
    draft['creative_plan']['test_variants'] = [{'title': '另一个角度', 'cover_copy': '替代封面'}]
    contract = build_reader_contract(TASK, draft)
    assert not any(key.startswith('variant:') for keys in contract['units'].values() for key in keys)


def test_contract_changes_when_actual_draft_changes_and_is_not_selected_by_draft():
    a = build_reader_contract(TASK, DRAFT)
    draft = {**DRAFT, 'title': '只改了标题', 'reader_intent': 'aesthetic_emotion'}
    b = build_reader_contract(TASK, draft)
    assert a['contract_id'] != b['contract_id']
    assert b['intent'] == 'tutorial'


def test_local_edit_carries_conflict_instead_of_silently_expanding_scope():
    scope = {'allowed_surfaces': ['body:0'], 'instruction': '只改第一段，标题图片保留'}
    contract = build_reader_contract(TASK, DRAFT, edit_scope=scope)
    raw = judgement(contract, gap=True)
    raw['units'][0]['findings'][0]['repair_surfaces'] = ['title']
    assessed = validate_reader_value(raw, contract, marketing_public_surfaces(DRAFT))
    assert assessed['status'] == 'revise'
    assert assessed['scope_conflicts'] == ['card:c1.body', 'title']
    brief = reader_revision_brief(assessed, contract)
    assert '只可修改：body:0' in brief and '范围外字段' in brief and '保留原稿' in brief


def test_missing_reader_judgement_is_not_a_silent_legacy_pass_for_capable_client():
    class Missing(Client):
        async def _json(self, *args, **kwargs):
            self._calls += 1
            return marketing()
    client = Missing()
    with pytest.raises(ValueError, match='missing_reader_value_review'):
        asyncio.run(review_marketing(client, task=TASK, draft=DRAFT, evidence={}))
    assert client._calls == 1


def test_same_judge_reader_pass_cannot_override_an_unverified_product_claim():
    class Unsupported(Client):
        async def _json(self, *args, **kwargs):
            result = await super()._json(*args, **kwargs)
            result['dimensions']['product'] = {'status': 'unverified', 'rationale': '没有该能力证据',
                'findings': [{'region': 'body:0', 'quote': '', 'reason': '待核产品能力', 'action': '修订这条主张'}]}
            result['revision_brief'] = '仅修产品主张'
            return result
    result = asyncio.run(review_marketing(Unsupported(), task=TASK, draft=DRAFT, evidence={}))
    assert result['reader_value']['status'] == 'pass' and result['decision'] == 'revise'


def test_format_correction_cannot_remove_a_reader_gap_or_change_selected_intent():
    contract = build_reader_contract(TASK, DRAFT)
    a = {**marketing(), 'reader_value': judgement(contract, gap=True)}
    b = {**marketing(), 'reader_value': judgement(contract, gap=False)}
    with pytest.raises(ValueError, match='reader judgement'):
        _preserve_review_judgements(a, b)
