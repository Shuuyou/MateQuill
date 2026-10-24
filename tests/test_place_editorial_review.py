"""Scene delivery must appear in public copy, not just in strategy metadata."""
import asyncio

import pytest

from app.llm import OpenAICompatibleLLM
from app.models import ContentBlock, EvidenceBundle, RunResult


@pytest.mark.parametrize('review', [None,
    {'matched': False, 'quote': '', 'reason': '正文只有通用续航卖点'},
    {'matched': True, 'quote': '下课后的社团合照', 'reason': '模型编造了正文中没有的引句'},
    {'matched': True, 'quote': '路过海棠广场来看看', 'reason': '只出现在CTA'}])
def test_generic_or_unverified_scene_match_requires_repair(review):
    model = OpenAICompatibleLLM(api_key='test-unused')
    model.local_editorial_context = {'audience': '周边大学生', 'scene': '下课后整理社团活动照片'}
    async def response(*args, **kwargs):
        return {'verdict': 'no_product_claims', 'audit': [], 'scene_review': review}
    model._json = response
    result = RunResult(status='ready', mode='local', blocks=[ContentBlock(text='包里的充电线可以歇一歇。')],
                       cta='路过海棠广场来看看')
    audit = asyncio.run(model.verify(result, EvidenceBundle(product='手机')))
    assert any(row.category == 'place_editorial' and row.severity == 'error' for row in audit)


def test_actual_local_action_is_accepted_without_forcing_a_place_name():
    model = OpenAICompatibleLLM(api_key='test-unused')
    model.local_editorial_context = {'audience': '周边大学生', 'scene': '下课后整理社团活动照片'}
    quote = '下课后的社团合照，先别急着删。'
    async def response(*args, **kwargs):
        return {'verdict': 'no_product_claims', 'audit': [], 'scene_review': {
            'matched': True, 'quote': quote, 'reason': '正文展现所选校园活动中的真实生活动作'}}
    model._json = response
    result = RunResult(status='ready', mode='local', blocks=[ContentBlock(text=quote)])
    audit = asyncio.run(model.verify(result, EvidenceBundle(product='手机')))
    assert any(row.category == 'place_editorial' and row.severity == 'info' for row in audit)
    assert not any(row.severity == 'error' for row in audit)
