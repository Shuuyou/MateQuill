"""Current selected facts must outrank numbers in historical style examples."""
import asyncio
from app.llm import OpenAICompatibleLLM
from app.models import TaskInput, EvidenceBundle, Fact


def test_agent_selected_weight_not_old_screen_example(monkeypatch):
    model = OpenAICompatibleLLM('https://example.test', 'test-only', 'mock')
    captured = {}
    async def capture(system, payload, **kwargs):
        captured.update(payload)
        return {}
    monkeypatch.setattr(model, '_json', capture)
    task = TaskInput(product='合成电脑', audience='本店好友', platform='moments',
        scene='换一个重量细节', requirements='不要重复屏幕主题\n本次选定事实ID：["weight"]\n本店与本人的工作记忆：旧标题14.2英寸屏幕')
    bundle = EvidenceBundle(product=task.product, facts=[
        Fact(id='screen', product=task.product, label='屏幕', value='14.2英寸', quote='14.2英寸', source_id='s'),
        Fact(id='weight', product=task.product, label='重量', value='970g', quote='约970g', source_id='s'),
    ])
    asyncio.run(model.generate(task, bundle, {'priority_fact_ids': ['screen', 'weight']}))
    assert [fact['id'] for fact in captured['evidence']['facts']] == ['weight']
