"""Divergent ideation stays bounded, account-local and grounded in supplied IDs."""
import asyncio
from copy import deepcopy
import json

import pytest

from app.creative_exploration import explore_creative_directions
from app.models import Asset, EvidenceBundle, TaskInput


class Model:
    def __init__(self, output=None, error=None):
        self.output = output
        self.error = error
        self.calls = []

    async def _json(self, system, payload, temperature):
        self.calls.append((system, payload, temperature))
        if self.error:
            raise self.error
        return deepcopy(self.output)


def response():
    return {'candidates': [
        {'id': 'detail', 'mechanism': 'detail_reveal', 'angle': '从耳机配色的小细节开始',
         'opening': '先看看包里的这一点颜色', 'payoff': '解释已有材料中的配色细节',
         'visual_idea': '使用现有产品图放大颜色局部', 'fact_ids': ['f1'], 'asset_ids': ['a1']},
        {'id': 'emotion', 'mechanism': 'emotional_scene', 'angle': '下班路上的独处时间',
         'opening': '下班了，耳朵也想换个频道', 'payoff': '用随身物件回到自己的时间',
         'visual_idea': '提出下班场景的待拍摄分镜', 'fact_ids': [], 'asset_ids': []},
        {'id': 'humor', 'mechanism': 'humor', 'angle': '让包里的小物件开一个会',
         'opening': '通勤包今天又开物件大会', 'payoff': '用拟人表达产品的随身角色',
         'visual_idea': '在现有产品图旁排版对话文字', 'fact_ids': [], 'asset_ids': ['a1']},
        {'id': 'demo', 'mechanism': 'practical_demo', 'angle': '用一个日常动作解释颜色搭配',
         'opening': '衣服选好了，随身物件呢', 'payoff': '根据真实配色给出搭配建议',
         'visual_idea': '提出产品颜色与衣服的搭配图', 'fact_ids': ['f1'], 'asset_ids': ['a1']},
    ], 'selected_id': 'emotion', 'selection_reason': '本次目标受众是通勤人群，情绪入口与已有照片方向更贴合。'}


def run(model, *, strategy=None, brief=None, evidence=None):
    return asyncio.run(explore_creative_directions(model,
        TaskInput(product='HUAWEI FreeBuds Neo', audience='通勤人群', scene='下班回家的路上'),
        EvidenceBundle(product='HUAWEI FreeBuds Neo', assets=[
            Asset(id='a1', product='HUAWEI FreeBuds Neo', url='/image', source_id='s1', title='产品图')]),
        strategy or {}, brief or {'account_voice': {'tone': '自然短句'},
                                 'cultural_context': {'cases': [{'id': 'case-1'}]}},
        evidence=evidence if evidence is not None else {'facts': [
            {'id': 'f1', 'value': '星河银', 'qualifiers': []}]}))


def test_one_call_returns_validated_selected_concept_and_all_distinct_candidates():
    model = Model(response())
    result = run(model)
    assert len(model.calls) == 1
    assert result['status'] == 'selected'
    assert result['selected_concept']['id'] == 'emotion'
    assert len({row['mechanism'] for row in result['candidates']}) == 4
    assert model.calls[0][1]['account_voice']['tone'] == '自然短句'
    assert model.calls[0][1]['cultural_context']['cases'][0]['id'] == 'case-1'
    assert model.calls[0][1]['approved_facts'][0]['id'] == 'f1'
    result['selected_concept']['angle'] = 'changed'
    assert result['candidates'][1]['angle'] != 'changed'
    assert model.output == response()


@pytest.mark.parametrize('mutation', ['selected', 'duplicate_id', 'duplicate_angle', 'same_mechanism',
                                      'too_few', 'unknown_fact', 'unknown_asset', 'probability'])
def test_invalid_or_ungrounded_model_responses_never_become_selected(mutation):
    raw = response()
    if mutation == 'selected': raw['selected_id'] = 'invented'
    if mutation == 'duplicate_id': raw['candidates'][1]['id'] = 'detail'
    if mutation == 'duplicate_angle': raw['candidates'][1]['angle'] = raw['candidates'][0]['angle'] + '！！！'
    if mutation == 'same_mechanism':
        for candidate in raw['candidates']: candidate['mechanism'] = 'contrast'
    if mutation == 'too_few': raw['candidates'] = raw['candidates'][:3]
    if mutation == 'unknown_fact': raw['candidates'][0]['fact_ids'] = ['made-up']
    if mutation == 'unknown_asset': raw['candidates'][0]['asset_ids'] = ['made-up']
    if mutation == 'probability': raw['candidates'][0]['viral_probability'] = 0.98
    model = Model(raw)
    result = run(model)
    assert result['status'] == 'fallback'
    assert result['selected_concept'] is None and result['candidates'] == []
    assert len(model.calls) == 1


def test_empty_evidence_cannot_be_replaced_by_ids_in_style_context():
    result = run(Model(response()), evidence={'facts': []},
                 brief={'account_voice': {'fact_ids': ['f1']},
                        'cultural_context': {'facts': [{'id': 'f1', 'value': 'some example'}]}})
    assert result['reason_code'] == 'unknown_concept_reference'


@pytest.mark.parametrize('error,code', [
    (RuntimeError('https://secret.example/?key=CANARY'), 'concept_service_unavailable'),
    (asyncio.TimeoutError(), 'concept_timeout'),
])
def test_failed_provider_is_explicit_and_does_not_leak_details(error, code):
    result = run(Model(error=error))
    assert result['status'] == 'fallback' and result['reason_code'] == code
    assert 'CANARY' not in json.dumps(result)


def test_caller_cancellation_is_not_swallowed():
    with pytest.raises(asyncio.CancelledError):
        run(Model(error=asyncio.CancelledError()))


def test_repair_and_small_shared_budget_do_not_consume_a_call():
    model = Model(response())
    assert run(model, strategy={'repair': True})['status'] == 'skipped'
    assert model.calls == []
    model.max_calls, model._calls = 5, 0
    assert run(model)['reason_code'] == 'call_budget_reserved'
    assert model.calls == []
    model.max_calls, model._calls = 6, 1
    assert run(model)['reason_code'] == 'call_budget_reserved'
    assert model.calls == []
    model._calls = 0
    assert run(model)['status'] == 'selected'
    assert len(model.calls) == 1


def test_context_is_bounded_without_mutating_or_retaining_other_account_state():
    first = Model(response())
    brief = {'account_voice': {'tone': 'A' * 16000}, 'cultural_context': {'cases': ['B' * 16000]}}
    original = deepcopy(brief)
    result = run(first, brief=brief)
    assert result['status'] == 'selected'
    payload = first.calls[0][1]
    assert payload['account_voice']['truncated'] and payload['cultural_context']['truncated']
    assert len(json.dumps(payload, ensure_ascii=False)) < 42000
    assert brief == original
    second = Model(response())
    run(second, brief={'account_voice': {'tone': 'another account'}})
    assert second.calls[0][1]['account_voice'] == {'tone': 'another account'}
    assert 'A' * 100 not in json.dumps(second.calls[0][1])


def system_requirements(*, brief='展示新品外观与日常聆听', required='新品体验活动', fresh='本次采用下班场景，轻松一点，保留表情。'):
    envelope = {
        'purpose': '为这家门店创作本次任务的独立内容',
        'brief': brief,
        'campaign': {'required_copy': required, 'source_refs': [{'excerpt': '历史开售说明'}]},
        'angle': '先留好这份清单',
        'narrative_device': '三张清单卡：目标、核对点、体验动作',
        'suggested_cta': '咨询旧选购需求',
        'visual_direction': '旧核对卡排版',
        'store_profile_user_supplied': {'audience': '旧校园受众', 'core_tasks': ['旧资料整理']},
    }
    opportunity = {'自主选题': {'scene': '过去的电子秤场景', 'title': '旧称重选题'},
                   '边界': '内部推测说明'}
    return json.dumps(envelope, ensure_ascii=False) + '\n' + json.dumps(opportunity, ensure_ascii=False) + '\n' + fresh


def test_active_requirements_projects_real_system_shape_without_mutating_task():
    from app.creative_exploration import active_creative_requirements
    task = TaskInput(scene='下班走出写字楼', goal='吸引通勤人群', requirements=system_requirements())
    original = deepcopy(task.model_dump())
    cleaned = active_creative_requirements(task)
    assert '展示新品外观与日常聆听' in cleaned
    assert '新品体验活动' in cleaned
    assert cleaned.startswith('当前补充要求：\n本次采用下班场景')
    for obsolete in ['清单', '核对点', '旧校园', '旧资料', '电子秤', '旧称重', '旧选购', '内部推测', '历史开售']:
        assert obsolete not in cleaned
    assert task.model_dump() == original
    assert active_creative_requirements({'requirements': cleaned}) == cleaned


def test_active_requirements_keeps_ordinary_briefs_verbatim():
    from app.creative_exploration import active_creative_requirements
    values = ['请帮我写三张清单卡，这是本次明确要求。\n保留这句原话🎧',
              '{"brief":"普通用户JSON","angle":"不要自动丢掉"}\n补充一些口语。',
              '{"purpose":"用户输入","brief":"没有campaign，不是系统信封"}',
              'A' * 5000]
    for value in values:
        assert active_creative_requirements({'requirements': value}) == value


def test_active_requirements_excludes_work_memory_and_all_later_history():
    from app.creative_exploration import active_creative_requirements
    value = system_requirements() + '\n本店与本人的工作记忆（只用于表达策略）：历史标题\n历史中的另一条要求'
    cleaned = active_creative_requirements({'requirements': value})
    assert '本次采用下班场景' in cleaned
    assert '历史标题' not in cleaned and '历史中的另一条要求' not in cleaned
    plain = '保留当前原始要求\n本店与本人的工作记忆：旧稿\n之后的历史内容'
    assert active_creative_requirements({'requirements': plain}) == '保留当前原始要求'


def test_latest_instruction_and_required_copy_survive_oversized_system_brief():
    from app.creative_exploration import active_creative_requirements
    required = '活动必须保留的原话' * 180
    fresh = '较早的补充。' * 500 + '最新要求：用轻松短句，不再使用旧选购方向。'
    cleaned = active_creative_requirements({'requirements': system_requirements(
        brief='旧背景' * 5000, required=required, fresh=fresh)})
    assert len(cleaned) <= 2200
    assert required in cleaned
    assert '最新要求：用轻松短句，不再使用旧选购方向。' in cleaned
    assert cleaned.index('最新要求') < cleaned.index(required)


def test_exploration_receives_current_scene_and_sanitized_latest_requirements():
    task = TaskInput(product='HUAWEI FreeBuds Neo', audience='通勤人群', scene='当前下班场景',
                     goal='当前吸引兴趣', requirements=system_requirements())
    bundle = EvidenceBundle(product=task.product, assets=[
        Asset(id='a1', product=task.product, url='/image', source_id='s1', title='产品图')])
    model = Model(response())
    result = asyncio.run(explore_creative_directions(model, task, bundle, {}, {},
        evidence={'facts': [{'id': 'f1', 'value': '星河银'}]}))
    assert result['status'] == 'selected'
    sent = model.calls[0][1]['task']
    assert sent['scene'] == '当前下班场景' and sent['goal'] == '当前吸引兴趣'
    assert '本次采用下班场景' in sent['requirements']
    assert '清单' not in sent['requirements'] and '电子秤' not in sent['requirements']
