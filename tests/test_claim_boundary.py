"""Sentence-level citation boundaries: advice is not a product specification."""
import asyncio
import pytest

from app.models import Fact, RunResult, TaskInput
from app.workflow import AgentWorkflow, has_product_claim
from test_workflow_adversarial import PRODUCT, Provider, draft, evidence


ADVICE=[
    '课表摊开，写下那门课要装的软件。',
    '课程软件能不能装、资料格式能不能打开。',
    '软件名、版本、运行条件列成三行，到店逐项问清支持情况。',
    '截图和课程资料都躺在手机里，坐下写作业前，先别急着选电脑。把资料分成截图、链接、文档三类，各写一句“最后要拿来做什么”，判断会清楚很多。',
    '采用清单整理资料，先列出3项待确认的问题。',
    '先确认是否支持华为分享，再问清兼容情况。',
    '不确定课程软件能否安装，先准备一份核对清单。',
    '先列出支持条件，到店问清哪些软件可以使用。',
]


@pytest.fixture
def short_values():
    return [Fact(id=str(i),product=PRODUCT,label='测试属性',value=value,quote=value,source_id='s1')
            for i,value in enumerate(['支持','是','有','1','3','软件','资料','电脑'])]


@pytest.mark.parametrize('text',ADVICE)
def test_short_fact_values_do_not_turn_advice_into_claim(text,short_values):
    assert not has_product_claim(text,short_values)


@pytest.mark.parametrize('text',[
    '内存32GB。','这台电脑搭载麒麟处理器。','本机支持所有课程软件。',
    '所有资料格式都能打开。','售价5999元。','屏幕为OLED。','采用OLED屏幕。',
    '触控：支持。','HarmonyOS 6.1','先核对软件能不能装，但这台支持所有软件。',
    '先列一张软件清单。这台电脑兼容所有专业应用。',
    '是否支持跨设备剪贴板需要核对，不过本机内置全部办公软件。',
])
def test_product_assertions_and_mixed_advice_still_require_citation(text,short_values):
    assert has_product_claim(text,short_values)


def test_real_local_product_boolean_values_do_not_contaminate_advice():
    from app.local_knowledge import LocalKnowledgeProvider
    bundle=asyncio.run(LocalKnowledgeProvider().retrieve('HUAWEI MateBook Pro','课程软件 资料格式'))
    if not bundle.facts:
        pytest.skip('Private local archive is not installed in this environment')
    assert len(bundle.facts)>=18
    assert any(f.value=='支持' for f in bundle.facts)
    assert all(not has_product_claim(text,bundle.facts) for text in ADVICE)
    assert has_product_claim('本机支持所有课程软件，内存32GB。',bundle.facts)


def test_uncited_claim_messages_locate_body_card_and_original_sentence():
    content=draft()
    content['blocks'].append({'text':'先列软件清单。这台支持所有课程软件。','fact_ids':[]})
    content['creative_plan']={'cards':[{'id':'software-check','headline':'先问清楚',
        'body':'课表摊开，写下那门课要装的软件。本机搭载全新处理器。','fact_ids':[]} ]}
    result=RunResult.model_validate(content)
    task=TaskInput(product=PRODUCT,audience='学生',scene='选电脑',platform='moments',mode='live')
    messages=[a.message for a in AgentWorkflow(Provider(evidence()))._audit(task,result,evidence()) if a.category=='citation']
    assert any('正文第2段' in x and '这台支持所有课程软件' in x for x in messages)
    assert any('创意卡片 software-check' in x and '本机搭载全新处理器' in x for x in messages)
    assert not any('写下那门课' in x or '先列软件清单' in x for x in messages)
