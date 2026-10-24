"""A failed prerequisite must not turn into repeated work or transport retries."""
import asyncio

import pytest
from fastapi import HTTPException

from app.gtm_agent import jobs
from app.gtm_agent.repository import get_turn
from test_gtm_agent_core import identities, acting, start, scripted

pytestmark = pytest.mark.real_tenancy


def decision(tool='generate_content', **args):
    return {'action': 'tool', 'tool': tool, 'arguments': args}


def execute(owner, planner, tools):
    _, turn = start(owner)
    asyncio.run(jobs.execute_claimed(jobs.claim_next(), planner=planner, tools_factory=tools))
    with acting(owner):
        return get_turn(_['id'], turn['id'])


def test_identical_invalid_call_is_not_executed_again(identities):
    calls = []
    class Tools:
        def __init__(self, *_): pass
        async def execute(self, name, args):
            calls.append((name, args))
            raise HTTPException(422, {'code': 'product_evidence_required',
                                      'required_tool': 'read_product'})
    planner = scripted(decision(product='phone'), decision(product='phone'))
    result = execute(identities[0], planner, Tools)
    assert len(calls) == 1
    assert result['status'] == 'failed'
    assert result['error']['code'] == 'tool_repeat_blocked'
    assert planner.calls[1]['tool_recovery']['required_tool'] == 'read_product'


def test_successful_prerequisite_allows_original_call(identities):
    calls = []
    class Tools:
        def __init__(self, *_): self.ready = False
        async def execute(self, name, args):
            calls.append(name)
            if name == 'read_product':
                self.ready = True
                return {'product': 'phone', 'facts': []}
            if not self.ready:
                raise HTTPException(422, '先读取准确产品资料再创作')
            return {'artifact': {'id': 'created', 'kind': 'run', 'title': '完成'}}
    planner = scripted(decision(product='phone'), decision('read_product', product='phone'),
                       decision(product='phone'))
    result = execute(identities[0], planner, Tools)
    assert calls == ['generate_content', 'read_product', 'generate_content']
    assert result['status'] == 'completed'
    assert planner.calls[2]['tool_recovery'] is None


def test_changed_arguments_remain_repairable(identities):
    calls = []
    class Tools:
        def __init__(self, *_): pass
        async def execute(self, name, args):
            calls.append(args)
            if args['product'] == 'wrong':
                raise HTTPException(422, '型号不匹配')
            return {'artifact': {'id': 'created', 'kind': 'run', 'title': '完成'}}
    result = execute(identities[0], scripted(decision(product='wrong'), decision(product='right')), Tools)
    assert len(calls) == 2 and result['status'] == 'completed'


@pytest.mark.parametrize('failure', ['http_timeout', 'terminal_read_timeout'])
def test_transport_failure_stops_without_editorial_retry(identities, failure):
    calls = []
    class Tools:
        def __init__(self, *_): pass
        async def execute(self, name, args):
            calls.append(name)
            if failure == 'http_timeout':
                raise HTTPException(504, '生成超时')
            return {'status': 'blocked', 'terminal': True, 'issues': [
                {'category': 'model', 'message': '模型生成或内容审核未完成（ReadTimeout）。需求已保留，可重新生成。'}]}
    planner = scripted(decision())
    result = execute(identities[0], planner, Tools)
    assert calls == ['generate_content'] and len(planner.calls) == 1
    assert result['error']['code'] == 'model_transport_error'
    assert '审核' not in result['message']


def test_editorial_rejection_keeps_editorial_reason(identities):
    class Tools:
        def __init__(self, *_): pass
        async def execute(self, *_):
            return {'terminal': True, 'issues': [{'category': 'marketing', 'message': '需要更具体的切角'}]}
    result = execute(identities[0], scripted(decision()), Tools)
    assert result['error']['code'] == 'creation_review_unresolved'


def test_context_restoration_happens_before_planning(identities):
    called = []
    class Tools:
        def __init__(self, *_): self.restored = False
        async def restore_context(self):
            self.restored = True
            called.append('restore')
            return [{'tool': 'read_product', 'result': {'product': 'phone', 'facts': []}}]
        def context_summary(self):
            assert self.restored
            return {'ready_products': ['phone']}
        async def execute(self, *_):
            called.append('generate')
            return {'artifact': {'id': 'created', 'kind': 'run', 'title': '完成'}}
    planner = scripted(decision(product='phone'))
    result = execute(identities[0], planner, Tools)
    assert result['status'] == 'completed' and called == ['restore', 'generate']
    assert planner.calls[0]['tool_context']['ready_products'] == ['phone']
    assert planner.calls[0]['observations'][0]['tool'] == 'read_product'
