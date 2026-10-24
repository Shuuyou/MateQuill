"""Creative cards share body evidence checks and the existing model-call budget."""
import asyncio

import pytest

from app.llm import OpenAICompatibleLLM
from app.models import AuditItem, TaskInput
from app.workflow import AgentWorkflow
from test_workflow_adversarial import PRODUCT, Provider, Model, draft, evidence


def task(**overrides):
    return TaskInput(product=PRODUCT, audience='学生', scene='课堂学习', platform='moments', mode='live', **overrides)


def card(**overrides):
    return {'id': 'card-1', 'role': 'checklist', 'headline': '先看使用条件',
            'body': '内存16GB，需兼容设备。', 'layout': 'text_card', 'visual_intent': '文字核对卡',
            'asset_ids': [], 'fact_ids': ['f1'], 'disclosures': ['需兼容设备'],
            'truth_status': 'design_proposal', 'block_index': 0, **overrides}


def run_with_card(row):
    # Directly exercise the hard boundary independently of connector schemas;
    # generated plans are normalized before this method in production.
    from app.models import RunResult
    result=RunResult.model_validate({**draft(), 'creative_plan': {'cards': [row]}})
    return AgentWorkflow(Provider(evidence()))._audit(task(), result, evidence())


@pytest.mark.parametrize('mutation,category', [
    ({'body': '内存32GB，需兼容设备。'}, 'number'),
    ({'fact_ids': ['missing']}, 'citation'),
    ({'body': '内存16GB。', 'disclosures': []}, 'qualifier'),
    ({'headline': '亲测闭眼买'}, 'compliance'),
    ({'disclosures': ['需兼容设备', '补贴500元']}, 'qualifier'),
    ({'asset_ids': ['missing']}, 'image'),
])
def test_cards_cannot_bypass_fact_or_source_audit(mutation, category):
    assert any(x.severity=='error' and x.category==category for x in run_with_card(card(**mutation)))


def test_old_model_output_is_compatible_but_no_storyboard_claim():
    result=asyncio.run(AgentWorkflow(Provider(evidence()), Model(draft())).run(task()))
    assert result.status=='ready'
    assert result.creative_plan=={}
    assert any(x.category=='creative' and '未生成' in x.message for x in result.audit)


def test_unknown_connector_is_a_safe_block():
    result=asyncio.run(AgentWorkflow(Provider(evidence()), Model(draft())).run(task(creative_connector='missing.connector')))
    assert result.status=='blocked'
    assert any(x.category=='creative' for x in result.audit)


def test_test_variant_cannot_hide_uncited_product_claim():
    from app.models import RunResult
    result=RunResult.model_validate({**draft(), 'creative_plan': {'cards': [],
        'test_variants':[{'title':'搭载32GB内存','cover_copy':'亲测全专业兼容'}]}})
    issues=AgentWorkflow(Provider(evidence()))._audit(task(),result,evidence())
    assert any(x.category=='citation' and x.severity=='error' for x in issues)
    assert any(x.category=='compliance' and x.severity=='error' for x in issues)


class CaptureModel(OpenAICompatibleLLM):
    def __init__(self):
        super().__init__(api_key='synthetic-unused')
        self.payloads=[]

    async def _json(self, system, payload, temperature=0.2):
        self.payloads.append(payload)
        if payload.get('schema', {}).get('title') == 'MarketingReview':
            from app.marketing_review import DIMENSIONS
            value = {
                'schema_version': '1', 'phase': payload['phase'], 'decision': 'accept',
                'dimensions': {key: {'status': 'pass', 'rationale': '合成夹具验证数据流', 'findings': []}
                               for key in DIMENSIONS},
                'creative_effectiveness': {'status': 'pass', 'hook': '合成夹具的具体任务',
                    'local_value': '夹具保留原有场景', 'brand_connection': '引用给定产品',
                    'natural_action': '保留原有行动建议', 'distinctiveness': '保留卡片结构',
                    'preserve': ['测试中的完整卡片'], 'improvements': []},
                'revision_brief': '',
            }
            if payload.get('reader_value_contract'):
                from test_reader_value import judgement
                value['reader_value'] = judgement(payload['reader_value_contract'])
            return value
        if 'draft' in payload:
            result = {'audit': [{'severity':'info','category':'semantic','message':'所有产品主张均被引用直接支持'}]}
            if 'product_claim_schema' in payload:
                # This synthetic draft makes the same supported RAM claim in
                # its body and card; all other surfaces contain editorial copy.
                surfaces = payload['product_claim_surfaces']
                claims = [{'surface': key, 'quote': text,
                    'proposition': '内存为16GB', 'claim_type': 'spec',
                    'evidence_ids': ['f1'],
                    'evidence_quotes': [{'evidence_id': 'f1', 'quote': '内存16GB。需兼容设备。'}],
                    'support_level': 'exact_spec', 'verdict': 'supported',
                    'reason': '合成规格原文直接支持，并保留兼容条件'}
                    for key, text in surfaces.items() if '16GB' in text]
                result['product_claim_review'] = {'claims': claims,
                    'coverage': {key: 'claims_listed' if '16GB' in text else 'no_product_claim'
                                 for key, text in surfaces.items()}}
            return result
        return draft()


def test_writer_uses_connector_and_verifier_receives_entire_card_plan():
    from app.models import RunResult
    model=CaptureModel()
    asyncio.run(model.generate(task(),evidence(),{}))
    payload=model.payloads[-1]
    assert payload['creative_brief']['fact_budget']==1
    assert payload['platform_playbook']==payload['creative_brief']['playbook']
    result=RunResult.model_validate({**draft(), 'creative_plan': {'cards':[card()]}})
    asyncio.run(model.verify(result,evidence()))
    semantic_payload = next(payload for payload in reversed(model.payloads) if 'assessment_scope' in payload)
    assert semantic_payload['draft']['creative_plan']['cards'][0]['body']==card()['body']
    assert 'f1' in [x['id'] for x in semantic_payload['evidence']['facts']]
    marketing_payload = next(payload for payload in reversed(model.payloads)
                             if payload.get('schema', {}).get('title') == 'MarketingReview')
    assert marketing_payload['draft']['creative_plan']['cards'][0]['body']==card()['body']


def test_creative_card_repair_stays_one_cycle():
    class RepairModel(Model):
        def __init__(self):
            super().__init__(draft())
            self.calls=[]
        async def generate(self,*args):
            self.calls.append('generate')
            row=draft()
            row['blocks'][0]['text']='内存999GB，需兼容设备。'
            return row
        async def verify(self,*args):
            self.calls.append('verify')
            return [AuditItem(severity='info',category='semantic',message='所有产品主张均被引用直接支持')]
        async def repair(self,*args):
            self.calls.append('repair')
            return draft()
    model=RepairModel()
    result=asyncio.run(AgentWorkflow(Provider(evidence()),model).run(task()))
    assert model.calls==['generate','verify','repair','verify']
    assert result.status=='ready'


def test_registered_platform_runs_without_new_generator_branch(monkeypatch):
    from app.creative_connectors import core, EditorialConnector, register_connector
    monkeypatch.setattr(core,'_REGISTRY',dict(core._REGISTRY))
    monkeypatch.setattr(core,'_DEFAULTS',dict(core._DEFAULTS))
    # Extension rules must exist when the base builds/validates its brief, not
    # be appended after super().build_brief() has already rejected missing rules.
    rule={'name':'测试渠道','min_images':0,'max_images':3,
          'structure':'任务→证据→行动','tone':'简洁','cta':'核对需求'}
    register_connector(EditorialConnector('lab_channel','测试渠道',
        profile={'fact_budget':1,'platform_rule':rule},playbooks=[]),default=True)
    new_task=task().model_copy(update={'platform':'lab_channel'})
    result=asyncio.run(AgentWorkflow(Provider(evidence()),Model(draft())).run(new_task))
    assert result.status=='ready'
    assert result.strategy['name']=='测试渠道'


def test_malformed_plan_still_blocks_after_one_repair():
    class BadPlanModel(Model):
        async def generate(self,*args):
            return {**draft(),'creative_plan':{'cards':[{'id':'bad','block_index':-1}]}}
        async def repair(self,*args):
            return await self.generate(*args)
    result=asyncio.run(AgentWorkflow(Provider(evidence()),BadPlanModel(draft())).run(task()))
    assert result.status=='blocked'
    assert any(x.category=='creative_schema' and x.severity=='error' for x in result.audit)


@pytest.mark.parametrize('plan',[None,{}, {'cards':[]}])
def test_creative_capable_writer_cannot_omit_storyboard(plan):
    class CreativeModel(Model):
        supports_creative_plan=True
        async def repair(self,*args):
            return self.content
    result=asyncio.run(AgentWorkflow(Provider(evidence()),CreativeModel({**draft(),'creative_plan':plan})).run(task()))
    assert result.status=='blocked'
    assert any(x.category=='creative_missing' for x in result.audit)


def test_compatibility_check_advice_does_not_reintroduce_boolean_fact_value():
    from app.workflow import has_product_claim
    from app.models import Fact
    boolean_fact=Fact(id='touch',product=PRODUCT,label='触摸屏 / 触控',value='支持',
                      quote='触控：支持',source_id='s1')
    tips=[
        '截图和课程资料都躺在手机里，坐下写作业前，先别急着选电脑。把资料分成截图、链接、文档三类，各写一句“最后要拿来做什么”，判断会清楚很多。',
        '第一件要核对的是课程软件：软件名、版本、运行条件列成三行，到店逐项问清支持情况。这一步没过，其他先放一放。',
    ]
    assert all(not has_product_claim(text,[boolean_fact]) for text in tips)
    # Appending advice cannot hide a separate affirmative claim.
    assert has_product_claim('先问清支持情况。这台电脑支持所有课程软件。',[boolean_fact])
    assert has_product_claim('先确认是否支持。内存32GB。',[boolean_fact])
    assert has_product_claim('触控：支持。',[boolean_fact])
