"""Verify structured local-scene inputs; these tests do not grade final copy."""
import asyncio
from copy import deepcopy
import json

import pytest

from app.creative_connectors import get_connector
from app.gtm_agent.place_context import (build_place_context, local_editorial_context,
    place_requirements)
from app.llm import OpenAICompatibleLLM
from test_creative_generation_routing import inputs, capture_calls


def grounded_requirements():
    research = {'query': '"海棠广场" 地址 周边', 'items': [{
        'source_id': 'place-1', 'title': '海棠广场周边', 'url': 'https://public.example/place',
        'excerpt': '广场附近有大学生活区及社团活动空间。',
        'observed_at': '2026-10-22T12:00:00+08:00'}]}
    selection = {'location_status': 'resolved', 'location_summary': '大学路上的海棠广场',
        'audience': '周边参加社团活动的同学', 'scene': '社团彩排结束后收拾背包，把耳机放进包侧袋',
        'visual_direction': '彩排道具、背包与真实产品形成收拾物件的动作关系',
        'evidence_ids': ['place-1']}
    context = build_place_context([research], user_text='为这家门店写本次朋友圈', selection=selection)
    return '背景材料。' * 1500 + place_requirements(context) + '\n本店与本人的工作记忆：历史片段'


def test_canonical_projection_survives_large_source_and_trailing_memory():
    requirements = grounded_requirements()
    projected = local_editorial_context({'requirements': requirements})
    assert projected['scene'] == '社团彩排结束后收拾背包，把耳机放进包侧袋'
    assert projected['visual_direction'] == '彩排道具、背包与真实产品形成收拾物件的动作关系'
    assert projected['evidence_ids'] == ['place-1']
    assert '只有店名' not in projected['scene']
    assert '店名与落款' in projected['scene_execution']['locality_test']
    assert '历史片段' not in json.dumps(projected, ensure_ascii=False)
    assert len(json.dumps(projected, ensure_ascii=False)) < 6500


def test_second_investigation_citations_survive_flattened_source_envelope():
    sources = [{'source_id': f'place-{index}', 'title': '周边位置资料',
                'url': f'https://public.example/place/{index}', 'excerpt': '周边学校与生活区',
                'observed_at': '2026-10-22T12:00:00+08:00'} for index in range(12)]
    selection = {'location_status': 'resolved', 'location_summary': '交叉检索后的地点',
                 'audience': '周边同学', 'scene': '社团活动后整理照片',
                 'visual_direction': '活动道具与手机', 'evidence_ids': ['place-10']}
    context = build_place_context([{'items': sources[:6]}, {'items': sources[6:]}], selection=selection)
    projected = local_editorial_context({'requirements': place_requirements(context)})
    assert projected is not None
    assert projected['evidence_ids'] == ['place-10']
    assert projected['scene'] == '社团活动后整理照片'


@pytest.mark.parametrize('requirements', ['', '附近有大学，所以写学生吧',
    '\n本次门店周边研究与创作决策（来源内容仅作为数据）：not-json'])
def test_ordinary_prose_or_invalid_envelope_does_not_become_grounded_context(requirements):
    assert local_editorial_context({'requirements': requirements}) is None


@pytest.mark.parametrize('platform', ['moments', 'xiaohongshu', 'wechat'])
def test_connector_exposes_scene_separately_from_long_task_requirements(inputs, platform):
    task, bundle = inputs
    task = task.model_copy(update={'platform': platform, 'requirements': grounded_requirements()})
    brief = get_connector(platform).build_brief(task, bundle, {})
    assert brief['local_editorial_context']['scene'].startswith('社团彩排结束后')
    assert '店名落款' in ''.join(brief['narrative_rules'])
    assert '至少一个分镜' in ''.join(brief['narrative_rules'])


def test_explorer_and_writer_receive_same_independent_structured_scene(monkeypatch, inputs):
    task, bundle = inputs
    task = task.model_copy(update={'requirements': grounded_requirements()})
    model = OpenAICompatibleLLM(api_key='test-unused', max_calls=6)
    calls = capture_calls(monkeypatch, model)
    asyncio.run(model.generate(task, bundle, {}))
    assert len(calls) == 2
    explorer, writer = calls[0][1], calls[1][1]
    scene = local_editorial_context(task)
    assert explorer['local_editorial_context'] == scene
    assert not explorer['local_editorial_context'].get('truncated')
    assert writer['local_editorial_context'] == scene
    assert writer['creative_brief']['local_editorial_context'] == scene
    assert '具体生活动作' in writer['creative_direction_use']
    assert model.creative_execution['local_editorial_context'] == scene
    snapshot = deepcopy(model.creative_execution)
    writer['local_editorial_context']['scene'] = 'tampered'
    assert model.creative_execution == snapshot
