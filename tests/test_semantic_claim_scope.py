"""Semantic verdict concerns actual claims, never completeness of the evidence store."""
import asyncio

import pytest

from app.llm import OpenAICompatibleLLM, ModelCallError
from app.models import RunResult
from test_workflow_adversarial import evidence


class Verifier(OpenAICompatibleLLM):
    def __init__(self, response):
        super().__init__(api_key='unused-test-key')
        self.response=response
        self.prompt=''
        self.payload={}

    async def _json(self, system, payload, temperature=0.2):
        self.prompt, self.payload=system,payload
        return self.response


def verify(response):
    model=Verifier(response)
    draft=RunResult(blocks=[{'text':'课表摊开，写下那门课要装的软件。'}])
    return model,asyncio.run(model.verify(draft,evidence()))


def test_advice_only_has_explicit_no_claims_verdict_without_more_evidence():
    model,items=verify({'verdict':'no_product_claims','audit':[
        {'severity':'info','category':'semantic','message':'课程软件核对是行动建议。'}]})
    assert not any(x.severity=='error' for x in items)
    assert any('无需产品参数引用' in x.message for x in items)
    assert model.payload['assessment_scope']['knowledge_base_completeness']=='out_of_scope'
    assert '同一句中若另有' in model.prompt
    assert '逐字引用有问题的短句' in model.prompt


@pytest.mark.parametrize('verdict',['supported','no_product_claims'])
def test_positive_verdict_does_not_override_a_specific_error(verdict):
    _,items=verify({'verdict':verdict,'audit':[
        {'severity':'error','category':'semantic','message':'卡片c2：“保证兼容”缺少依据。'}]})
    assert any(x.severity=='error' for x in items)
    assert not any('所有产品主张均被引用直接支持' in x.message for x in items)


def test_negative_verdict_cannot_be_passed_by_missing_error_item():
    _,items=verify({'verdict':'unsupported','audit':[
        {'severity':'warning','category':'semantic','message':'需检查。'}]})
    assert any(x.severity=='error' for x in items)


def test_unknown_semantic_verdict_fails_closed():
    with pytest.raises(ModelCallError):
        verify({'verdict':'probably','audit':[{'severity':'info','category':'semantic','message':'看起来不错'}]})


def test_explicit_verdict_can_have_no_findings_but_empty_legacy_cannot_pass():
    _,items=verify({'verdict':'no_product_claims','audit':[]})
    assert any('核验通过' in x.message for x in items)
    with pytest.raises(ModelCallError):
        verify({'audit':[]})
