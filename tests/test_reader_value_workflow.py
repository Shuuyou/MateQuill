"""Reader gaps use the existing one-repair loop and persist the actual verdict."""
import asyncio
from copy import deepcopy

from app.marketing_review import review_marketing, review_audit_items
from app.models import AuditItem, TaskInput
from app.workflow import AgentWorkflow
from test_marketing_generation_loop import Provider, draft, PRODUCT
from test_reader_value import marketing, judgement


class ReaderModel:
    enabled = True
    supports_reader_value_contract = True
    model = 'synthetic-reader-loop'
    max_calls = 4

    def __init__(self, *, resolve=True):
        self._calls = self.repairs = 0
        self.resolve = resolve
        self.marketing_review_history = []
        self.received_repair = None

    async def generate(self, task, bundle, strategy):
        self.task = task
        return draft('先想用途，再看内存')

    async def _json(self, system, payload, temperature):
        self._calls += 1
        value = marketing()
        value['reader_value'] = judgement(payload['reader_value_contract'], gap=not (self.repairs and self.resolve))
        return value

    async def verify(self, result, bundle):
        self.marketing_review = await review_marketing(self, task=self.task,
            draft=result.model_dump(), evidence=bundle.model_dump())
        self.marketing_review_history.append(deepcopy(self.marketing_review))
        return [AuditItem(severity='info', category='semantic', message='所有产品主张有直接支持，核验通过'),
                *review_audit_items(self.marketing_review)]

    async def repair(self, task, bundle, strategy, result):
        self.repairs += 1
        self.received_repair = deepcopy(strategy.get('reader_value'))
        value = draft('先想用途，再看内存')
        value['blocks'][0]['text'] = '内存16GB。先想课程文件与软件怎样一起用，再聊你的选择。'
        return value


def run(model):
    task = TaskInput(product=PRODUCT, audience='关注本店的朋友', scene='讲一项选购取舍',
                     goal='让人知道怎样做选择', platform='moments', mode='live')
    return asyncio.run(AgentWorkflow(Provider(), model).run(task))


def test_reader_gap_gets_one_existing_repair_then_persists_same_budget_review_history():
    model = ReaderModel()
    result = run(model)
    assert result.status == 'ready'
    assert model.repairs == 1 and model._calls == 2
    assert model.received_repair['review']['status'] == 'revise'
    assert model.received_repair['revision_brief']
    saved = result.strategy['reader_value']
    assert saved['review']['status'] == 'pass'
    assert [v['status'] for v in saved['history']] == ['revise', 'pass']
    assert saved['contract']['contract_id'] == saved['review']['contract_id']
    assert result.images[0].asset_id == 'a1'
    assert result.title == '先想用途，再看内存'


def test_unresolved_reader_gap_never_becomes_ready_after_one_repair():
    model = ReaderModel(resolve=False)
    result = run(model)
    assert result.status == 'blocked'
    assert model.repairs == 1 and model._calls == 2
    assert result.strategy['reader_value']['review']['status'] == 'revise'
    assert any(a.category == 'reader_value' and a.severity == 'error' for a in result.audit)
