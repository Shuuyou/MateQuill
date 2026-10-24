"""Server provider failures remain machine-readable through the content boundary."""
import asyncio

import pytest

from app.gtm_agent import jobs
from app.gtm_agent.repository import get_turn, TURN
from app.gtm_agent.tools import AgentTools
from app.llm import ModelCallError
from app.models import TaskInput
from app.tenancy import authorize_resource
from app.workflow import AgentWorkflow
from test_gtm_agent_core import identities, acting, start, scripted, Provider as ToolProvider
from test_moments_workflow import Model, Provider, PRODUCT


@pytest.mark.parametrize('code,status', [('model_http_error', 524), ('model_http_error', 401),
                                        ('model_http_error', 429), ('ReadTimeout', None)])
def test_workflow_preserves_only_safe_provider_machine_fields(code, status):
    class FailingModel(Model):
        async def generate(self, *args):
            raise ModelCallError(code, status)
    task = TaskInput(product=PRODUCT, audience='关注本店的朋友', scene='展示今天的桌面',
                     platform='moments', mode='live')
    result = asyncio.run(AgentWorkflow(Provider(), FailingModel()).run(task))
    assert result.status == 'blocked'
    assert result.strategy['generation_error'] == {'code': code, **({'http_status': status} if status else {})}
    assert any(issue.category == 'model' and issue.severity == 'error' for issue in result.audit)


@pytest.mark.real_tenancy
@pytest.mark.parametrize('status', [524, 503, 401, 403, 429])
def test_provider_http_failure_survives_real_tool_and_runtime(identities, status):
    owner = identities[0]
    conversation, turn = start(owner)
    calls = []
    async def generator(task):
        calls.append(task)
        return {'status': 'blocked', 'strategy': {'generation_error': {
            'code': 'model_http_error', 'http_status': status,
            'response_body': 'private provider payload', 'endpoint': 'https://private.example'}},
            'audit': [{'severity': 'error', 'category': 'model',
                       'message': f'模型生成或内容审核未完成（model_http_error HTTP {status}）。'}]}
    planner = scripted({'action': 'tool', 'tool': 'read_catalog'},
        {'action': 'tool', 'tool': 'read_product', 'arguments': {'product': 'HUAWEI MateBook Pro'}},
        {'action': 'tool', 'tool': 'generate_content', 'arguments': {
            'product': 'HUAWEI MateBook Pro', 'audience': '附近居民', 'scene': '整理课程笔记'}})
    asyncio.run(jobs.execute_claimed(jobs.claim_next(), planner=planner,
        tools_factory=lambda c, t: AgentTools(c, t, provider=ToolProvider(), generator=generator)))
    with acting(owner):
        result = get_turn(conversation['id'], turn['id'])
        saved = authorize_resource(TURN, turn['id'])['body']
    assert len(calls) == 1 and len(planner.calls) == 3
    assert result['status'] == 'failed'
    assert result['error'] == {'code': 'model_transport_error', 'provider_code': 'model_http_error', 'http_status': status}
    assert '超时' not in result['message'] and '调整' not in result['message'] and '审核' not in result['message']
    observation = saved['observations'][-1]['result']
    assert observation['error'] == {'code': 'model_http_error', 'http_status': status}
    assert 'response_body' not in observation['error'] and 'endpoint' not in observation['error']
    assert '调整' not in observation['message']
