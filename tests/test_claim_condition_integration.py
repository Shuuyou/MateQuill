import asyncio
from copy import deepcopy

from app.llm import OpenAICompatibleLLM
from app.models import RunResult, EvidenceBundle


def test_semantic_payload_contains_exact_cited_screen_fact_and_scope_candidates():
    evidence = EvidenceBundle.model_validate({'product': '测试手机', 'facts': [
        {'id': 'screen', 'product': '测试手机', 'label': '屏幕尺寸', 'value': '6.75 英寸',
         'quote': '显示屏按标准矩形测量时对角线长度6.75英寸。', 'source_id': 's1',
         'qualifiers': ['圆角设计，实际可视区域略小。']},
        {'id': 'camera', 'product': '测试手机', 'label': '后置摄像头', 'value': '潜望式长焦；电影效果需升级。',
         'quote': '支持潜望式长焦。电影效果需HOTA升级支持。', 'source_id': 's1',
         'qualifiers': ['需HOTA升级支持。', '最大']}],
         'sources': [{'id': 's1', 'title': '测试资料', 'excerpt': '完整原文'}]})
    result = RunResult.model_validate({'blocks': [
        {'text': '屏幕6.75英寸。', 'fact_ids': ['screen'], 'disclosures': ['圆角设计，实际可视区域略小。']},
        {'text': '用长焦把远处灯光拉近拍。', 'fact_ids': ['camera']} ]})

    class Model(OpenAICompatibleLLM):
        async def _json(self, system, payload, temperature=0.2):
            self.payload = deepcopy(payload)
            scope = []
            for candidate in payload['missing_condition_candidates']:
                scope.append({key: candidate[key] for key in ('surface', 'fact_id', 'qualifier', 'claim_quote', 'applicable')}
                             | {'reason': '该段仅主张长焦拍摄动作，未主张电影效果或最大数值。'})
            return {'verdict': 'supported', 'audit': [], 'condition_scope': scope}

    model = Model()
    asyncio.run(model.verify(result, evidence))
    screen = next(f for f in model.payload['evidence']['facts'] if f['id'] == 'screen')
    assert screen['value'] == '6.75 英寸'
    assert any('6.75' in span['text'] for span in model.payload['evidence']['evidence_spans'])
    assert len(model.condition_exemptions) == 2
    assert model.condition_scope_diagnostics['accepted_count'] == 2
    assert result.strategy['condition_scope_diagnostics']['accepted_count'] == 2
    assert all(e['claim_quote'] == result.blocks[1].text for e in model.condition_exemptions)
