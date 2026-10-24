"""Task-dependent voice stays source-bound and reaches the actual writer call."""
import asyncio
from copy import deepcopy

import pytest

from app.editorial import editorial_review
from app.editorial_voice import build_editorial_voice
from app.models import EvidenceBundle, RunResult, TaskInput


def test_photo_atoms_use_only_actual_visible_context_without_ocr_authority_or_private_fields():
    materials = [{'id': 'own-photo', 'kind': 'photo', 'observations': {
        'caption': '电脑放在帆布袋旁，桌上有没收好的充电线。', 'scene_hints': ['共享长桌'],
        'visible_text': ['全场立减999元'], 'product_candidates': ['猜测型号'],
        'private_url': 'https://private.example/image?token=hidden'}},
        {'id': 'not-a-photo', 'kind': 'audio', 'transcript': '未验证的音频字段'}]
    saved = deepcopy(materials)
    context = build_editorial_voice(TaskInput(platform='moments', scene='换教室前收包'), user_materials=materials)
    assert len(context['observation_atoms']) == 1
    atom = context['observation_atoms'][0]
    assert atom['material_id'] == 'own-photo'
    assert atom['caption_excerpt'] == materials[0]['observations']['caption']
    assert atom['scene_hints'] == ['共享长桌']
    assert all(value not in str(context) for value in ('999元', '猜测型号', 'private.example', 'hidden'))
    assert materials == saved


def test_real_wording_remains_expression_data_not_a_fake_staff_experience_or_template():
    task = TaskInput(platform='moments', scene='下课收电脑', requirements='这个线怎么又打结了……保留这句，别替我卖惨。')
    context = build_editorial_voice(task, account_voice={'publisher_identity': 'salesperson'})
    assert context['publisher_identity'] == 'salesperson'
    assert context['current_request']['text'] == task.requirements
    assert context['observation_atoms'] == []
    assert context['scene_proposal']['basis'] == 'editorial_scenario_not_claimed_event'
    assert context['selected_concept'] is None
    assert 'probability' not in context and 'score' not in context


def test_current_task_and_selected_concept_are_bound_without_importing_model_or_media_internals():
    concept = {'angle': '一团线的收尾', 'opening': '线还没收好，人已经要走了', 'payoff': '一个收包动作',
               'visual_idea': '帆布袋与充电线', 'internal_prompt': 'secret'}
    context = build_editorial_voice(TaskInput(scene='收包'), concept=concept,
        local_context={'scene': '课间把桌上东西装进袋子', 'private_source_body': 'private'})
    assert context['selected_concept']['opening'] == concept['opening']
    assert context['scene_proposal']['text'] == '课间把桌上东西装进袋子'
    assert 'secret' not in str(context) and 'private_source_body' not in str(context)


def test_fragment_projection_redacts_credentials_and_contacts_without_mutating_original():
    text = '请保留节奏，联系电话13812345678，邮箱a@example.com，密码:secret'
    task = TaskInput(requirements=text)
    context = build_editorial_voice(task)
    assert all(value not in context['current_request']['text'] for value in ('13812345678', 'a@example.com', 'secret'))
    assert task.requirements == text


@pytest.mark.parametrize('scene,title,body', [
    ('给数码收纳一个明确建议', '线各归各位', '把常用的两根线留在袋子外侧的小口袋。要走的时候，不用把整包东西倒出来。'),
    ('观察桌面的一点光', '桌边的下午', '杯子在右边，电脑往左挪了一点。光刚好落在空出来的桌面上。'),
])
def test_delivered_advice_and_observation_do_not_require_manufactured_comment_bait(scene, title, body):
    task = TaskInput(platform='xiaohongshu', scene=scene)
    result = RunResult(title=title, blocks=[{'text': body}], cta='')
    notes = editorial_review(task, result)
    assert not any('互动问题' in item.message for item in notes)


def test_writer_receives_same_source_bound_voice_and_execution_snapshot_without_extra_calls(monkeypatch):
    from app.llm import OpenAICompatibleLLM
    import app.creative_exploration as exploration
    import app.marketing_context as marketing

    async def context(*args):
        return {'publisher': {}}

    async def explore(*args, **kwargs):
        return {'status': 'selected', 'selected_concept': {
            'id': 'scene', 'angle': '收包的小动作', 'opening': '收包先收线',
            'payoff': '一个具体收包动作', 'visual_idea': '充电线与帆布袋'}, 'candidates': []}

    monkeypatch.setattr(marketing, 'prepare_marketing_context', context)
    monkeypatch.setattr(exploration, 'explore_creative_directions', explore)
    captured = []

    class Writer(OpenAICompatibleLLM):
        def __init__(self):
            super().__init__(api_key='synthetic-unused', max_calls=6)

        async def _json(self, system, payload, temperature=.2):
            captured.append((system, payload))
            return {'title': '收包先收线'}

    model = Writer()
    task = TaskInput(platform='moments', scene='下课收包', requirements='轻松一点，结尾停住就好')
    bundle = EvidenceBundle(product='合成产品', user_materials=[{'id': 'p1', 'kind': 'photo',
        'observations': {'caption': '桌上有帆布袋与充电线'}}])
    asyncio.run(model.generate(task, bundle, {}))
    assert len(captured) == 1
    _, payload = captured[0]
    assert payload['editorial_voice'] == model.creative_execution['editorial_voice']
    assert payload['editorial_voice']['observation_atoms'][0]['material_id'] == 'p1'
    assert payload['editorial_voice']['selected_concept']['opening'] == '收包先收线'
    assert payload['evidence']['facts'] == []
