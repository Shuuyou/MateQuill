"""Check the actual writer input, without making a model-service request."""
import asyncio
import copy

from app.llm import OpenAICompatibleLLM
from app.models import EvidenceBundle, Fact, Source, TaskInput


def test_ecosystem_writer_prefers_scoped_capability_and_preserves_source_conditions(monkeypatch):
    app_conditions = ['以上应用以具体软件版本和实际支持情况为准。']
    collaboration_conditions = ['在手机/平板、电脑上，开启 WLAN、蓝牙，并登录同一华为账号。',
                                '在手机/平板、电脑上，开启跨设备剪贴板的开关。']
    app_value = '特色应用包括笔记、文件管理及资料整理。'
    collaboration_value = '使用跨设备剪贴板，在设备间复制粘贴文本、图片等素材。'
    app_quote = app_value + '\n' + '\n'.join(app_conditions)
    collaboration_quote = collaboration_value + '\n' + '\n'.join(collaboration_conditions)
    bundle = EvidenceBundle(product='HUAWEI MateBook Pro', facts=[
        Fact(id='apps', product='HUAWEI MateBook Pro', label='特色应用', value=app_value,
             quote=app_quote, qualifiers=app_conditions, source_id='apps-source'),
        Fact(id='clipboard', product='HUAWEI MateBook Pro', label='生态协同/跨设备剪贴板',
             value=collaboration_value, quote=collaboration_quote, qualifiers=collaboration_conditions,
             source_id='collaboration-source'),
    ], sources=[Source(id='apps-source', title='应用原文', excerpt=app_quote),
                Source(id='collaboration-source', title='协同步骤原文', excerpt=collaboration_quote)])
    original_bundle = copy.deepcopy(bundle.model_dump())
    task = TaskInput(product=bundle.product, ecosystem_products=['HUAWEI Mate 80'],
                     audience='职场人', scene='手机收集资料，到电脑整理文件与笔记', platform='moments')
    model = OpenAICompatibleLLM(api_key='unit-test-placeholder')
    captured = {}

    async def capture(system, payload, temperature):
        captured.update(payload)
        return {'title': 'test'}

    monkeypatch.setattr(model, '_json', capture)
    asyncio.run(model.generate(task, bundle, {'priority_fact_ids': ['apps']}))
    evidence = captured['evidence']
    # Moments grants one publishable fact: the relevant scoped procedure must
    # outrank even a suggested generic application list.
    assert [fact['id'] for fact in evidence['facts']] == ['clipboard']
    span_by_id = {span['id']: span for span in evidence['evidence_spans']}
    fact = evidence['facts'][0]
    assert span_by_id[fact['evidence_span_id']]['text'] == collaboration_value
    assert [span_by_id[sid]['text'] for sid in fact['qualifier_span_ids']] == collaboration_conditions
    assert bundle.model_dump() == original_bundle
    assert bundle.facts[0].qualifiers == app_conditions


def test_application_fact_remains_available_with_its_exact_footnote(monkeypatch):
    condition = '应用支持范围以具体版本及实际使用条件为准。'
    value = '特色应用包括笔记与资料整理。'
    bundle = EvidenceBundle(product='HUAWEI MateBook Pro', facts=[
        Fact(id='apps', product='HUAWEI MateBook Pro', label='特色应用', value=value,
             quote=value + condition, qualifiers=[condition], source_id='s1'),
    ], sources=[Source(id='s1', title='应用原文', excerpt=value + condition)])
    task = TaskInput(product=bundle.product, audience='学生', scene='整理课堂笔记', platform='moments')
    model = OpenAICompatibleLLM(api_key='unit-test-placeholder')
    captured = {}

    async def capture(system, payload, temperature):
        captured.update(payload)
        return {'title': 'test'}

    monkeypatch.setattr(model, '_json', capture)
    asyncio.run(model.generate(task, bundle, {}))
    fact = captured['evidence']['facts'][0]
    assert fact['id'] == 'apps' and fact['qualifiers'] == [condition]
    assert any(span['kind'] == 'verbatim_condition' and span['text'] == condition
               for span in captured['evidence']['evidence_spans'])
