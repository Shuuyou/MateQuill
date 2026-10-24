"""Paid design attempts only reopen after an explicit completed marketing review."""
import asyncio
from contextlib import contextmanager
from copy import deepcopy
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from app.gtm_agent import tools as module
from app.models import TaskInput


@pytest.fixture
def harness(monkeypatch):
    from app import api, design_service
    task = TaskInput(product='HUAWEI Mate 80', audience='本次编辑选择的读者',
                     scene='旧叙事中的场景', platform='moments', requirements='来源与产品条件保留')
    original = {'status': 'ready', 'run_id': 'original-run', '_poster_task': task.model_dump()}
    resources = {(module.TURN, 'turn-1'): {'body': {'effects': {}}},
                 ('run', 'original-run'): {'body': original}}
    tools = object.__new__(module.AgentTools)
    tools.turn = {'id': 'turn-1'}
    tools.conversation = {}
    tools.audience = 'private'
    tools.opportunity = None
    tools.reference = lambda: {'kind': 'run', 'id': 'original-run', 'body': original}
    effects, rewrites, designs = [], [], []

    @contextmanager
    def bind(turn_id, key, audience, *, intermediate=False):
        effects.append((turn_id, key, audience, intermediate))
        yield

    async def rewrite(task, request):
        rewrites.append(deepcopy(task))
        result = {'status': 'ready', 'run_id': 'rewritten-run', '_poster_task': task.model_dump()}
        resources[('run', 'rewritten-run')] = {'body': result}
        return result

    monkeypatch.setattr(module, 'authorize_resource', lambda kind, identifier: deepcopy(resources[kind, identifier]))
    monkeypatch.setattr(module, 'bind_effect', bind)
    monkeypatch.setattr(module, 'artifact_reference', lambda kind, identifier, **kw:
                        {'kind': kind, 'id': identifier, 'title': '成品'})
    monkeypatch.setattr(api, 'run_task', rewrite)
    monkeypatch.setattr(design_service, 'load_design', lambda identifier, **kw:
                        deepcopy(resources['design', identifier]['body']))
    args = module.Arguments(platform='moments', instruction='制作一张朋友圈配图')
    return SimpleNamespace(tools=tools, args=args, task=task, rewrites=rewrites, designs=designs,
                           effects=effects, monkeypatch=monkeypatch, design_service=design_service,
                           resources=resources)


def reject(code='marketing_reselect'):
    return HTTPException(422, {'code': code, 'feedback': ['不要替读者定义消费能力；改用当地生活观察']})


def test_completed_marketing_rejection_rewrites_copy_then_creates_one_replacement(harness):
    h = harness
    async def design(parameters):
        h.designs.append(deepcopy(parameters))
        if len(h.designs) == 1:
            raise reject()
        h.resources['design', 'replacement-design'] = {'body': {
            'id':'replacement-design', 'title':'成品', 'status':'ready',
            'source_run_id':parameters['source_run_id']}}
        return {'id': 'replacement-design', 'title': '成品'}
    h.monkeypatch.setattr(h.design_service, 'execute_design', design)
    result = asyncio.run(h.tools.generate_design('create_design_bundle', h.args))
    assert result['artifact']['id'] == 'replacement-design'
    assert result['source_run_id'] == 'rewritten-run'
    assert len(h.rewrites) == 1 and len(h.designs) == 2
    assert h.rewrites[0].product == h.task.product
    assert h.rewrites[0].scene != h.task.scene
    assert '来源与产品条件保留' in h.rewrites[0].requirements
    assert '不要替读者定义消费能力' in h.rewrites[0].requirements
    assert h.designs[1]['source_run_id'] == 'rewritten-run'
    assert h.designs[1]['parent_id'] is None
    assert h.designs[0]['design_id'] != h.designs[1]['design_id']
    assert len({effect[1] for effect in h.effects}) == 3
    assert [effect[3] for effect in h.effects] == [False, True, False]
    assert h.tools.conversation['current_artifact'] == result['artifact']


@pytest.mark.parametrize('detail', [
    {'code': 'review_incomplete', 'message': '图片已保存，审核未完成'},
    {'code': 'marketing_reselect', 'feedback': ['unfinished transport cannot authorize a new image']},
    '图片已保存，成图检查暂未完成，请稍后重试',
])
def test_unfinished_review_never_starts_reselection(harness, detail):
    h = harness
    async def design(parameters):
        h.designs.append(deepcopy(parameters))
        raise HTTPException(503, detail)
    h.monkeypatch.setattr(h.design_service, 'execute_design', design)
    with pytest.raises(HTTPException) as caught:
        asyncio.run(h.tools.generate_design('create_design_bundle', h.args))
    assert caught.value.status_code == 503
    assert h.rewrites == [] and len(h.designs) == 1
    assert len(h.effects) == 1
    assert 'current_artifact' not in h.tools.conversation


def test_second_marketing_rejection_propagates_without_another_rewrite_or_image(harness):
    h = harness
    async def design(parameters):
        h.designs.append(deepcopy(parameters))
        raise reject()
    h.monkeypatch.setattr(h.design_service, 'execute_design', design)
    with pytest.raises(HTTPException) as caught:
        asyncio.run(h.tools.generate_design('create_design_bundle', h.args))
    assert caught.value.detail['code'] == 'marketing_reselect'
    assert len(h.rewrites) == 1 and len(h.designs) == 2
    assert len(h.effects) == 3
    assert 'current_artifact' not in h.tools.conversation


def test_incomplete_rewritten_copy_never_starts_replacement_image(harness):
    h = harness
    from app import api
    async def blocked(task, request):
        h.rewrites.append(deepcopy(task))
        return {'status': 'blocked', 'run_id': 'blocked-run'}
    async def design(parameters):
        h.designs.append(deepcopy(parameters))
        raise reject()
    h.monkeypatch.setattr(api, 'run_task', blocked)
    h.monkeypatch.setattr(h.design_service, 'execute_design', design)
    with pytest.raises(HTTPException) as caught:
        asyncio.run(h.tools.generate_design('create_design_bundle', h.args))
    assert caught.value.detail['code'] == 'marketing_reselection_incomplete'
    assert len(h.rewrites) == 1 and len(h.designs) == 1
