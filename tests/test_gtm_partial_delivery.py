"""Text checkpoints cannot turn an unfinished paid image request into success."""
import asyncio

import pytest
from fastapi import HTTPException
from sqlalchemy import select

from app.gtm_agent import jobs
from app.gtm_agent.effects import bind_effect, checkpoint_generated_artifact
from app.gtm_agent.repository import CONVERSATION, TURN, conversation_row, get_turn
from app.gtm_agent.runtime import run_turn
from app.tenancy import authorize_resource, register_resource, service_update_body, transaction_context
from app.tenancy.database import db_connection
from test_gtm_agent_core import identities, acting, start, scripted

pytestmark = pytest.mark.real_tenancy


@pytest.mark.parametrize('image_result', ['http_blocked', 'blocked', 'none', 'worker_completed_run'])
def test_claimed_turn_keeps_partial_failure_and_committed_text(identities, image_result, monkeypatch):
    owner = identities[0]
    conversation, turn = start(owner, '准备文案和配图')
    plan = {'turn_id':turn['id'], 'deliverables':['content','images'], 'artifacts':[]}
    executed = []

    class Tools:
        opportunity = {'deliverables':['content']} if image_result == 'worker_completed_run' else plan
        def __init__(self, *_): pass
        async def execute(self, name, args):
            executed.append(name)
            if name == 'generate_content':
                with transaction_context():
                    register_resource('run', 'partial-text', body={'status':'ready','title':'已完成文案'})
                    with bind_effect(turn['id'], 'partial-text-effect', 'private'):
                        checkpoint_generated_artifact('run','partial-text',{'title':'已完成文案'})
                return {'artifact':{'kind':'run','id':'partial-text','title':'已完成文案'}}
            if image_result == 'http_blocked':
                raise HTTPException(422, '图片审校未通过')
            return {'status':'blocked'} if image_result == 'blocked' else None

    with acting(owner):
        row = conversation_row(conversation['id'])
        service_update_body(CONVERSATION, row['id'], {**row['body'],'current_opportunity':plan}, expected_version=row['version'])
    if image_result == 'worker_completed_run':
        async def legacy_worker(*args, **kwargs):
            result = await Tools().execute('generate_content', {})
            return {'status':'completed', 'artifact':result['artifact'], 'message':'作品已完成','questions':[]}
        monkeypatch.setattr('app.gtm_agent.runtime.run_turn', legacy_worker)
    asyncio.run(jobs.execute_claimed(jobs.claim_next(),
        planner=scripted({'action':'tool','tool':'generate_content'},
            {'action':'tool','tool':'create_design_bundle'}), tools_factory=Tools))
    with acting(owner):
        result = get_turn(conversation['id'],turn['id'])
        assert result['status'] == 'failed'
        assert result['artifact']['id'] == 'partial-text'
        assert result['error']['code'] == 'partial_delivery'
        saved = authorize_resource(TURN, turn['id'])['body']
        assert saved['artifact']['id'] == 'partial-text'
        with db_connection() as connection:
            assert connection.execute(select(jobs.dispatch.c.status).where(
                jobs.dispatch.c.id == turn['id'])).scalar_one() == 'failed'
        if image_result != 'worker_completed_run':
            resumed = asyncio.run(run_turn(turn['id'], tools_factory=Tools,
                planner=lambda *_: pytest.fail('An unknown/blocked paid attempt must not automatically restart')))
            assert resumed['status'] == 'failed'
            assert resumed['artifact']['id'] == 'partial-text'
    assert executed == (['generate_content'] if image_result == 'worker_completed_run'
        else ['generate_content','create_design_bundle'])


def test_budget_exhaustion_and_old_design_do_not_complete_new_text(identities):
    owner = identities[0]
    _, turn = start(owner, '准备文案和配图')
    class Tools:
        opportunity = {'deliverables':['content','images'],
            'artifacts':[{'kind':'design','id':'older-design'}]}
        def __init__(self, *_): pass
        async def execute(self, *_):
            return {'artifact':{'kind':'run','id':'newer-text','title':'新文案'}}
    with acting(owner):
        result = asyncio.run(run_turn(turn['id'], planner=scripted(
            {'action':'tool','tool':'generate_content'}), tools_factory=Tools, max_steps=1))
    assert result['status'] == 'failed'
    assert result['artifact']['id'] == 'newer-text'
    assert result['error']['code'] == 'partial_delivery'


def test_text_revision_scope_does_not_inherit_old_plan_images(identities):
    owner = identities[0]
    conversation, turn = start(owner, '只改第一段，其他文字和现有图片保持原样')
    old_plan = {'turn_id':'previous-turn', 'deliverables':['content','images'],
        'artifacts':[{'kind':'run','id':'old-text'}]}
    calls = []
    class Tools:
        opportunity = old_plan
        def __init__(self, *_): pass
        async def execute(self, name, args):
            calls.append(name)
            assert name == 'revise_content'
            with transaction_context():
                register_resource('run','local-revision',body={'status':'ready','title':'局部改稿'})
                with bind_effect(turn['id'],'text-only-revision','private'):
                    checkpoint_generated_artifact('run','local-revision',{'title':'局部改稿'})
            return {'artifact':{'kind':'run','id':'local-revision','title':'局部改稿'}}
    with acting(owner):
        row = conversation_row(conversation['id'])
        service_update_body(CONVERSATION,row['id'],{**row['body'],'current_opportunity':old_plan},expected_version=row['version'])
    asyncio.run(jobs.execute_claimed(jobs.claim_next(), tools_factory=Tools,
        planner=scripted({'action':'tool','tool':'revise_content'})))
    with acting(owner):
        saved = authorize_resource(TURN,turn['id'])['body']
        assert saved['status'] == 'completed'
        assert saved['delivery_scope'] == ['content']
        assert saved['artifact']['id'] == 'local-revision'
    assert calls == ['revise_content']
