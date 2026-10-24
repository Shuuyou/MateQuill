"""Behavior and restart tests for scoped conversational GTM orchestration."""
import asyncio
from contextlib import contextmanager
from uuid import uuid4
import time
from concurrent.futures import ThreadPoolExecutor

import pytest
from fastapi import HTTPException
from sqlalchemy import update, select

from app.gtm_agent import jobs, memory
from app.gtm_agent.repository import create_conversation, get_conversation, get_turn, TURN, artifact_reference, link_artifact
from app.gtm_agent.models import TurnInput
from app.gtm_agent.effects import bind_effect, checkpoint_generated_artifact, _claim, assert_agent_lease
from app.gtm_agent.tools import AgentTools
from app.models import EvidenceBundle, Fact, Source
from app.store_campaigns import StoreInput, StoreRepository
from app.tenancy import set_context, reset_context, register_resource, authorize_resource, transaction_context
from app.tenancy.auth import new_session, context_from_session, session_row
from app.tenancy.database import clear_engine_cache, db_connection, memberships
from scripts.tenant_admin import bootstrap

pytestmark = pytest.mark.real_tenancy


@pytest.fixture
def identities(tmp_path, monkeypatch):
    monkeypatch.setenv('MATEQUILL_DATABASE_URL', 'sqlite:///'+(tmp_path/'agent.sqlite').as_posix())
    clear_engine_cache()
    result=[]
    for suffix in ('a','b'):
        account=bootstrap(email=f'agent-{uuid4().hex}@example.test',name='门店运营',
            tenant_name='企业'+suffix,password='Agent-Tests-Only-2026-Strong!')
        token=set_context(context_from_session(session_row(new_session(account['user_id'],account['tenant_id']))))
        try:
            store=StoreRepository().save_store(StoreInput(name='门店'+suffix,audience='附近居民'))
        finally:
            reset_context(token)
        raw=new_session(account['user_id'],account['tenant_id'],store['id'])
        result.append({'account':account,'store':store,'context':context_from_session(session_row(raw))})
    yield result
    clear_engine_cache()


@contextmanager
def acting(identity):
    token=set_context(identity['context'])
    try:
        yield
    finally:
        reset_context(token)


def start(owner,text='给我写一篇产品内容',audience='private'):
    with acting(owner):
        conversation=create_conversation(audience=audience)
        turn=jobs.enqueue_turn(conversation['id'],TurnInput(text=text,client_turn_id=uuid4().hex))
    return conversation,turn


class Provider:
    def catalog(self):
        return [{'name':'HUAWEI MateBook Pro'}]

    def retrieve(self,product,query):
        return EvidenceBundle(product=product,facts=[Fact(id='f1',product=product,label='功能',
            value='多屏协同',quote='支持多屏协同',source_id='s1')],sources=[Source(id='s1',title='官方资料')])


def scripted(*decisions):
    calls=[]
    def planner(system,payload):
        calls.append(payload)
        return decisions[len(calls)-1]
    planner.calls=calls
    return planner


def test_store_research_reaches_generation_and_survives_resume(identities):
    owner = identities[0]
    conversation, turn = start(owner, '请给海棠广场店写一条朋友圈')
    captured = []
    evidence = [{'source_id': 'place-school', 'title': '海棠广场交通介绍', 'url': 'https://public.example/place',
                 'excerpt': '海棠广场位于大学路，附近有大学学生生活区。'}]

    async def generate(task):
        captured.append(task)
        register_resource('run', 'store-researched', body={'status': 'ready', 'title': '下课之后'})
        return {'status': 'ready', 'run_id': 'store-researched', 'title': '下课之后'}

    planner = scripted(
        {'action': 'tool', 'tool': 'research_store', 'arguments': {'query': '"海棠广场" 地址 周边'}},
        {'action': 'tool', 'tool': 'ground_store', 'arguments': {'grounding': {
            'location_status': 'resolved', 'location_summary': '海棠广场位于大学路',
            'audience': '周边大学生', 'scene': '下课后整理课程资料',
            'visual_direction': '课程笔记与整理资料的真实动作', 'evidence_ids': ['place-school']}}},
        {'action': 'tool', 'tool': 'read_catalog'},
        {'action': 'tool', 'tool': 'read_product', 'arguments': {'product': 'HUAWEI MateBook Pro'}},
        {'action': 'tool', 'tool': 'generate_content', 'arguments': {
            'product': 'HUAWEI MateBook Pro', 'audience': '泛化人群', 'scene': '日常使用'}})
    asyncio.run(jobs.execute_claimed(jobs.claim_next(), planner=planner,
        tools_factory=lambda c, t: AgentTools(c, t, provider=Provider(), search=lambda _: evidence, generator=generate)))
    assert '大学学生生活区' in captured[0].requirements
    assert 'https://public.example/place' in captured[0].requirements
    assert '本次门店周边研究' in captured[0].requirements
    assert captured[0].audience == '周边大学生'
    assert captured[0].scene == '下课后整理课程资料'
    assert '课程笔记与整理资料的真实动作' in captured[0].requirements
    assert planner.calls[0]['store_profile']['name'] == owner['store']['name']
    with acting(owner):
        saved = authorize_resource(TURN, turn['id'])['body']
        restored = AgentTools(get_conversation(conversation['id']), saved)
        assert restored.store_research[0]['items'] == evidence
        assert get_turn(conversation['id'], turn['id'])['status'] == 'completed'


def test_store_research_no_results_is_explicit_not_a_customer_profile(identities):
    owner = identities[0]
    with acting(owner):
        conversation = create_conversation()
        tools = AgentTools(conversation, {'id': 'research-only', 'text': '写一条朋友圈'}, search=lambda _: [])
        result = asyncio.run(tools.execute('research_store', {'query': '"未知广场" 地址'}))
        assert result['state'] == 'no_results'
        assert result['items'] == []
        assert '不能证明微信好友' in result['use']
        assert result['profile']['name'] == owner['store']['name']


def test_idempotency_scope_and_active_turn(identities):
    owner,other=identities
    conversation,turn=start(owner)
    with acting(owner):
        original=authorize_resource(TURN,turn['id'])['body']['input']
        assert jobs.enqueue_turn(conversation['id'],TurnInput(**original))['id']==turn['id']
        with pytest.raises(HTTPException) as changed:
            jobs.enqueue_turn(conversation['id'],TurnInput(**{**original,'text':'改掉目标'}))
        assert changed.value.status_code==409
        with pytest.raises(HTTPException):
            jobs.enqueue_turn(conversation['id'],TurnInput(text='第二条',client_turn_id=uuid4().hex))
    with acting(other),pytest.raises(HTTPException):
        get_conversation(conversation['id'])


def test_real_tool_generation_and_model_selected_strategy(identities):
    owner=identities[0]
    conversation,turn=start(owner)
    calls=[]
    async def generate(task):
        calls.append(task)
        record={'run_id':'generated','status':'ready','title':'下班后的轻松时刻','intro':'内容','_poster_task':task.model_dump()}
        with transaction_context():
            register_resource('run','generated',body=record)
            checkpoint_generated_artifact('run','generated',record)
        return record
    planner=scripted(
        {'action':'tool','tool':'read_catalog'},
        {'action':'tool','tool':'read_product','arguments':{'product':'HUAWEI MateBook Pro'}},
        {'action':'tool','tool':'generate_content','arguments':{'product':'HUAWEI MateBook Pro','audience':'附近通勤职场人','scene':'下班后整理照片'}},
        {'action':'reply','message':'伪造一些销售成果'})
    asyncio.run(jobs.execute_claimed(jobs.claim_next(),planner=planner,
        tools_factory=lambda c,t:AgentTools(c,t,provider=Provider(),generator=generate)))
    with acting(owner):
        completed=get_turn(conversation['id'],turn['id'])
        assert completed['status']=='completed'
        assert completed['artifact']['id']=='generated'
        assert '销售成果' not in completed['message']
        assert get_conversation(conversation['id'])['current_artifact']['id']=='generated'
    assert len(calls)==1 and calls[0].audience=='附近通勤职场人'
    assert calls[0].store_id==owner['store']['id']
    assert len(planner.calls)==3  # No acknowledgement decision can regenerate the completed artifact.
    # Once a real artifact commits, no further planning/generation consumes
    # another credit or accidentally revises the just-created version.
    assert len(planner.calls)==3


def test_restart_reuses_atomically_checkpointed_artifact(identities):
    owner=identities[0]
    conversation,turn=start(owner)
    calls=[]
    async def interrupted(task):
        calls.append(task)
        record={'status':'ready','run_id':'survives-restart','title':'已完成'}
        with transaction_context():
            register_resource('run','survives-restart',body=record)
            checkpoint_generated_artifact('run','survives-restart',record)
        raise asyncio.CancelledError()
    planner=scripted({'action':'tool','tool':'read_catalog'},
        {'action':'tool','tool':'read_product','arguments':{'product':'HUAWEI MateBook Pro'}},
        {'action':'tool','tool':'generate_content','arguments':{'product':'HUAWEI MateBook Pro','audience':'居民','scene':'照片整理'}})
    with pytest.raises(asyncio.CancelledError):
        asyncio.run(jobs.execute_claimed(jobs.claim_next(),planner=planner,
            tools_factory=lambda c,t:AgentTools(c,t,provider=Provider(),generator=interrupted)))
    def forbidden(*args):
        raise AssertionError('Recovered committed result must not invoke a model')
    asyncio.run(jobs.execute_claimed(jobs.claim_next(),planner=forbidden))
    with acting(owner):
        assert get_turn(conversation['id'],turn['id'])['artifact']['id']=='survives-restart'
        assert get_turn(conversation['id'],turn['id'])['status']=='completed'
    assert len(calls)==1


def test_corrections_are_evidenced_superseded_and_available_next_task(identities):
    owner=identities[0]
    with acting(owner):
        first=memory.remember({'id':'t1','text':'不要夸张口吻'},kind='correction',key='tone',text='简洁自然',quote='不要夸张口吻')
        second=memory.remember({'id':'t2','text':'可以活泼但不要口号'},kind='correction',key='tone',text='活泼自然',quote='可以活泼但不要口号')
        assert authorize_resource('agent_memory',first['id'])['body']['superseded_by']==second['id']
        assert [m['id'] for m in memory.relevant_memories()]==[second['id']]
        assert memory.relevant_memories(audience='group')==[]
        with pytest.raises(HTTPException):
            memory.remember({'id':'bad','text':'你好'},kind='correction',key='tone',text='假偏好',quote='我没说过')
    conversation,turn=start(owner,'记得我之前的风格')
    planner=scripted({'action':'clarify','message':'这次要介绍哪款产品？','questions':[]})
    asyncio.run(jobs.execute_claimed(jobs.claim_next(),planner=planner))
    assert planner.calls[0]['working_memory'][0]['text']=='活泼自然'


def test_group_cannot_read_private_media_or_artifacts(identities):
    owner=identities[0]
    with acting(owner):
        group=create_conversation(audience='group')
        register_resource('media','private-photo',body={'id':'private-photo'})
        register_resource('run','private-work',body={'status':'ready','title':'私聊稿件'})
        with pytest.raises(HTTPException):
            jobs.enqueue_turn(group['id'],TurnInput(media_ids=['private-photo'],client_turn_id=uuid4().hex))
        with pytest.raises(HTTPException):
            artifact_reference('run','private-work',audience='group')


def test_stale_worker_fenced_and_revoked_identity_cancelled(identities):
    owner=identities[0]
    conversation,turn=start(owner)
    row=jobs.claim_next()
    with db_connection() as connection:
        connection.execute(update(jobs.dispatch).where(jobs.dispatch.c.id==turn['id']).values(lease_until=time.time()-1))
    replacement=jobs.claim_next()
    token=_claim.set((row['id'],row['claim_token']))
    try:
        with acting(owner),pytest.raises(HTTPException):
            assert_agent_lease()
    finally:
        _claim.reset(token)
    with db_connection() as connection:
        connection.execute(update(memberships).where(memberships.c.user_id==owner['account']['user_id']).values(active=False))
    asyncio.run(jobs.execute_claimed(replacement,planner=lambda *_:pytest.fail('Revoked session must not invoke model')))
    with db_connection() as connection:
        state=connection.execute(select(jobs.dispatch.c.status).where(jobs.dispatch.c.id==turn['id'])).scalar_one()
    assert state=='cancelled'


def test_blocked_generation_is_not_completed(identities):
    owner=identities[0]
    conversation,turn=start(owner)
    planner=scripted({'action':'tool','tool':'read_catalog'},
        {'action':'tool','tool':'read_product','arguments':{'product':'HUAWEI MateBook Pro'}},
        {'action':'tool','tool':'generate_content','arguments':{'product':'HUAWEI MateBook Pro','audience':'居民','scene':'日常办公'}},
        *[{'action':'reply'}]*3)
    asyncio.run(jobs.execute_claimed(jobs.claim_next(),planner=planner,
        tools_factory=lambda c,t:AgentTools(c,t,provider=Provider(),generator=lambda _: {'status':'blocked'})))
    with acting(owner):
        result=get_turn(conversation['id'],turn['id'])
        assert result['status']!='completed' and result['artifact'] is None


def test_revision_uses_real_current_artifact_and_latest_poster_version(identities):
    owner=identities[0]
    with acting(owner):
        conversation=create_conversation()
        register_resource('run','edited-original',body={'run_id':'edited-original','status':'ready',
            'title':'店员手工微调后的标题','_poster_task':{'product':'HUAWEI MateBook Pro'}})
        link_artifact(conversation['id'],'run','edited-original')
        turn=jobs.enqueue_turn(conversation['id'],TurnInput(text='标题短一点',client_turn_id=uuid4().hex))
    calls=[]
    async def revise(identifier,instruction):
        calls.append((identifier,instruction))
        record={'run_id':'revised','status':'ready','title':'新标题'}
        with transaction_context():
            register_resource('run','revised',body=record)
            checkpoint_generated_artifact('run','revised',record)
        return record
    planner=scripted({'action':'tool','tool':'read_artifact'},
        {'action':'tool','tool':'revise_content','arguments':{'instruction':'标题短一点'}}, {'action':'reply'})
    asyncio.run(jobs.execute_claimed(jobs.claim_next(),planner=planner,
        tools_factory=lambda c,t:AgentTools(c,t,reviser=revise)))
    assert calls==[('edited-original','标题短一点')]
    with acting(owner):
        assert get_turn(conversation['id'],turn['id'])['artifact']['id']=='revised'
        register_resource('poster','saved-poster',body={'id':'saved-poster','title':'海报','status':'draft','version':4})
        linked=link_artifact(conversation['id'],'poster','saved-poster')
        assert linked['current_artifact']['version']==4
        from app.tenancy import service_update_body
        service_update_body('poster','saved-poster',{'id':'saved-poster','status':'draft','version':5})
        assert get_conversation(conversation['id'])['current_artifact']['version']==5


def test_voice_only_turn_is_consumed_and_remembered(identities,monkeypatch):
    owner=identities[0]
    media_id=uuid4().hex
    class Media:
        def get(self,identifier):
            assert identifier==media_id
            return None,'audio/wav',{'kind':'audio','transcript':'以后别写官腔','observations':{}}
    monkeypatch.setattr('app.media_routes.media_store',lambda:Media())
    with acting(owner):
        register_resource('media',media_id,body={'id':media_id})
        conversation=create_conversation()
        turn=jobs.enqueue_turn(conversation['id'],TurnInput(media_ids=[media_id],client_turn_id=uuid4().hex))
    planner=scripted({'action':'tool','tool':'read_media'},
        {'action':'tool','tool':'remember','arguments':{'kind':'correction','key':'tone','text':'口语化，避免官腔','quote':'以后别写官腔'}},
        {'action':'reply'})
    asyncio.run(jobs.execute_claimed(jobs.claim_next(),planner=planner))
    assert planner.calls[1]['observations'][0]['result']['materials'][0]['transcript']=='以后别写官腔'
    with acting(owner):
        assert get_turn(conversation['id'],turn['id'])['status']=='completed'
        assert memory.relevant_memories()[0]['quote']=='以后别写官腔'


def test_concurrent_claim_is_exclusive_and_after_checkpoint_failure_keeps_result(identities):
    owner=identities[0]
    conversation,turn=start(owner)
    with ThreadPoolExecutor(max_workers=4) as pool:
        claimed=list(pool.map(lambda _:jobs.claim_next(),range(4)))
    rows=[row for row in claimed if row]
    assert len(rows)==1
    async def failed_response(task):
        record={'status':'ready','run_id':'committed-response-lost','title':'作品'}
        with transaction_context():
            register_resource('run',record['run_id'],body=record)
            checkpoint_generated_artifact('run',record['run_id'],record)
        raise RuntimeError('HTTP response interrupted after committed generation')
    planner=scripted({'action':'tool','tool':'read_catalog'},
        {'action':'tool','tool':'read_product','arguments':{'product':'HUAWEI MateBook Pro'}},
        {'action':'tool','tool':'generate_content','arguments':{'product':'HUAWEI MateBook Pro','audience':'居民','scene':'办公'}})
    asyncio.run(jobs.execute_claimed(rows[0],planner=planner,
        tools_factory=lambda c,t:AgentTools(c,t,provider=Provider(),generator=failed_response)))
    with acting(owner):
        result=get_turn(conversation['id'],turn['id'])
        assert result['status']=='completed' and result['artifact']['id']=='committed-response-lost'


def test_question_choices_survive_reload_and_clear_on_next_turn(identities):
    owner=identities[0]
    conversation,turn=start(owner,'想发新平板')
    planner=scripted({'action':'clarify','message':'这次是哪款？','questions':[
        {'field':'product','label':'选择平板型号','options':[{'value':'HUAWEI MatePad Edge','label':'MatePad Edge'}]}]})
    asyncio.run(jobs.execute_claimed(jobs.claim_next(),planner=planner))
    with acting(owner):
        restored=get_conversation(conversation['id'])
        assert restored['pending_questions'][0]['options'][0]['value']=='HUAWEI MatePad Edge'
        assert restored['last_turn_id']==turn['id']
        next_turn=jobs.enqueue_turn(conversation['id'],TurnInput(text='就是MatePad Edge',client_turn_id=uuid4().hex))
        restored=get_conversation(conversation['id'])
        assert restored['pending_questions']==[] and restored['last_turn_id']==next_turn['id']


def test_invalid_model_arguments_are_explainable_and_repaired(identities):
    owner=identities[0]
    conversation,turn=start(owner,'记住以后写自然短句')
    planner=scripted({'action':'tool','tool':'remember','arguments':{'scope':'store','kind':'correction',
            'key':'tone','text':'自然短句','quote':'以后写自然短句'}},
        {'action':'tool','tool':'remember','arguments':{'kind':'correction','key':'tone','text':'自然短句','quote':'以后写自然短句'}},
        {'action':'reply'})
    asyncio.run(jobs.execute_claimed(jobs.claim_next(),planner=planner))
    assert planner.calls[1]['observations'][0]['error'][0]=={'field':'scope','type':'extra_forbidden'}
    with acting(owner):
        assert get_turn(conversation['id'],turn['id'])['status']=='completed'


def test_creation_quota_429_stops_after_one_generator_and_preserves_reason(identities):
    owner = identities[0]
    conversation, turn = start(owner, '帮我写一条新品朋友圈')
    attempted = []
    quota_message = '保存到你的门店后，可以继续创作'

    async def exhausted(task):
        attempted.append(task)
        raise HTTPException(429, quota_message)

    generate = {'action': 'tool', 'tool': 'generate_content', 'arguments': {
        'product': 'HUAWEI MateBook Pro', 'audience': '本店好友', 'scene': '新品体验', 'platform': 'moments'}}
    planner = scripted({'action': 'tool', 'tool': 'read_catalog'},
        {'action': 'tool', 'tool': 'read_product', 'arguments': {'product': 'HUAWEI MateBook Pro'}},
        generate, generate, generate, generate)
    asyncio.run(jobs.execute_claimed(jobs.claim_next(), planner=planner,
        tools_factory=lambda c, t: AgentTools(c, t, provider=Provider(), generator=exhausted)))
    assert len(attempted) == 1 and len(planner.calls) == 3
    with acting(owner):
        result = get_turn(conversation['id'], turn['id'])
        assert result['status'] == 'failed' and result['artifact'] is None
        assert result['message'] == quota_message
        assert result['error'] == {'code': 'creation_limit_reached', 'http_status': 429}
        persisted = authorize_resource(TURN, turn['id'])['body']
        assert persisted['observations'][-1]['http_status'] == 429
        assert persisted['observations'][-1]['error'] == quota_message
        restored = get_conversation(conversation['id'])
        assert restored['active_turn_id'] is None
        assert restored['messages'][-1]['text'] == quota_message


def test_place_detector_understands_store_employee_suffix():
    from app.gtm_agent.place_context import requires_place_research
    assert requires_place_research({}, '我是华为旗舰店·南京德基店员，请写朋友圈')
    assert requires_place_research({}, '我是华为南京德基店')
    assert not requires_place_research({}, '我是店员，请介绍这款手机')


def test_research_expands_budget_and_invalid_decision_preserves_business_steps(identities):
    from app.gtm_agent.runtime import run_turn
    owner = identities[0]
    conversation, turn = start(owner, '写一条朋友圈')
    decisions = [{'action': 'not-a-valid-action'}] + [
        {'action': 'tool', 'tool': name} for name in [
            'read_catalog', 'research_store', 'read_catalog', 'read_catalog',
            'read_catalog', 'read_catalog', 'read_catalog', 'generate_content']]
    planner = scripted(*decisions)

    class Tools:
        store_research = []
        def __init__(self, *_): pass
        def store_profile(self): return {}
        async def execute(self, name, args):
            if name == 'research_store': self.store_research = [{'items': []}]
            if name == 'generate_content':
                return {'artifact': {'id': 'test-generated', 'kind': 'run', 'title': '真实作品'}}
            return {}
    with acting(owner):
        result = asyncio.run(run_turn(turn['id'], planner=planner, tools_factory=Tools))
    assert result['status'] == 'completed'
    assert len(planner.calls) == 9
    assert planner.calls[1]['steps_remaining'] == 8
    assert planner.calls[3]['steps_remaining'] == 8


def test_image_review_http_failure_does_not_reopen_paid_generation(identities):
    from app.gtm_agent.runtime import run_turn
    owner = identities[0]
    conversation, turn = start(owner, '制作配图')
    planner = scripted({'action': 'tool', 'tool': 'create_design_bundle'})
    attempted = []

    class Tools:
        def __init__(self, *_): pass
        async def execute(self, name, args):
            attempted.append(name)
            raise HTTPException(503, '图片已保存，成图检查暂未完成，请稍后重试')
    with acting(owner):
        result = asyncio.run(run_turn(turn['id'], planner=planner, tools_factory=Tools))
    assert attempted == ['create_design_bundle']
    assert len(planner.calls) == 1
    assert result['error']['code'] == 'image_attempt_stopped'


def test_explicit_step_limit_stays_bounded_when_research_appears(identities):
    from app.gtm_agent.runtime import run_turn
    owner = identities[0]
    conversation, turn = start(owner, 'write a post')
    planner = scripted({'action': 'tool', 'tool': 'research_store'})
    class Tools:
        store_research = []
        def __init__(self, *_): pass
        async def execute(self, name, args):
            self.store_research = [{'items': []}]
            return {}
    with acting(owner):
        result = asyncio.run(run_turn(turn['id'], planner=planner, tools_factory=Tools, max_steps=1))
    assert result['status'] == 'failed'
    assert len(planner.calls) == 1


def test_repeated_invalid_decisions_stop_at_three_calls(identities):
    from app.gtm_agent.runtime import run_turn
    owner = identities[0]
    conversation, turn = start(owner, 'write a post')
    planner = scripted(*[{'action': 'invalid'} for _ in range(3)])
    class Tools:
        def __init__(self, *_): pass
    with acting(owner):
        result = asyncio.run(run_turn(turn['id'], planner=planner, tools_factory=Tools))
    assert result['status'] == 'failed'
    assert len(planner.calls) == 3


def test_intermediate_reselection_checkpoint_preserves_effect_without_completing_image_turn(identities):
    owner = identities[0]
    conversation, turn = start(owner, 'create an image')
    rewritten = {'status': 'ready', 'title': 'Rewritten copy', 'run_id': 'reselection-run',
                 '_poster_task': {'product': 'HUAWEI Mate 80', 'audience': 'readers', 'scene': 'daily scene', 'platform': 'moments'}}
    design = {'status': 'ready', 'title': 'Final image', 'id': 'reselection-design', 'source_run_id': 'reselection-run'}
    with acting(owner):
        before = authorize_resource(TURN, turn['id'])
        with transaction_context():
            register_resource('run', 'reselection-run', body=rewritten)
            with bind_effect(turn['id'], 'text-reselection', 'private', intermediate=True):
                checkpoint_generated_artifact('run', 'reselection-run', rewritten)
        intermediate = authorize_resource(TURN, turn['id'])
        assert intermediate['body']['effects']['text-reselection']['id'] == 'reselection-run'
        assert intermediate['body'].get('artifact') is None
        assert intermediate['version'] == before['version'] + 1
        assert get_conversation(conversation['id']).get('current_artifact') is None
        with transaction_context():
            register_resource('design', 'reselection-design', body=design)
            with bind_effect(turn['id'], 'image-reselection', 'private'):
                checkpoint_generated_artifact('design', 'reselection-design', design)
        completed = authorize_resource(TURN, turn['id'])['body']
        assert completed['artifact']['kind'] == 'design'
        assert completed['artifact']['id'] == 'reselection-design'
        assert set(completed['effects']) == {'text-reselection', 'image-reselection'}


def test_malformed_planner_json_corrects_format_without_consuming_tool_step(identities):
    from app.gtm_agent.runtime import run_turn
    from app.llm import ModelCallError
    owner = identities[0]
    _, turn = start(owner, '完成这一条内容')
    payloads, executed = [], []

    def planner(system, payload):
        payloads.append(payload)
        if len(payloads) == 1:
            raise ModelCallError('invalid_model_json')
        return {'action':'tool','tool':'generate_content'}

    class Tools:
        def __init__(self, *_): pass
        async def execute(self, name, args):
            executed.append(name)
            return {'artifact':{'kind':'run','id':'format-corrected','title':'真实作品'}}

    with acting(owner):
        outcome = asyncio.run(run_turn(turn['id'], planner=planner, tools_factory=Tools, max_steps=1))
        saved = authorize_resource(TURN, turn['id'])['body']
    assert outcome['status'] == 'completed'
    assert executed == ['generate_content']
    assert len(payloads) == 2
    assert payloads[0]['steps_remaining'] == payloads[1]['steps_remaining'] == 1
    assert 'decision_format_correction' in payloads[1]
    assert saved['observations'][0]['error_code'] == 'invalid_model_json'
    assert 'raw' not in saved['observations'][0]


def test_malformed_planner_json_terminates_after_two_format_corrections(identities):
    from app.gtm_agent.runtime import run_turn
    from app.llm import ModelCallError
    owner = identities[0]
    _, turn = start(owner, '完成这一条内容')
    attempts, executed = [], []

    def planner(system, payload):
        attempts.append(payload)
        raise ModelCallError('invalid_model_json')

    class Tools:
        def __init__(self, *_): pass
        async def execute(self, name, args): executed.append(name)

    with acting(owner):
        result = asyncio.run(run_turn(turn['id'], planner=planner, tools_factory=Tools))
    assert result['status'] == 'failed'
    assert len(attempts) == 3  # Initial decision and at most two corrections.
    assert not executed


@pytest.mark.parametrize('code,http_status', [
    ('model_http_error',401), ('model_http_error',429), ('model_call_budget_exhausted',None),
    ('model_transport_error',None), ('invalid_model_json',401), ('invalid_model_json',429)])
def test_non_format_planner_failures_are_not_retried(identities, code, http_status):
    from app.gtm_agent.runtime import run_turn
    from app.llm import ModelCallError
    owner = identities[0]
    _, turn = start(owner)
    attempts = []
    def planner(*_):
        attempts.append(True)
        raise ModelCallError(code, http_status=http_status)
    with acting(owner):
        with pytest.raises(ModelCallError) as caught:
            asyncio.run(run_turn(turn['id'], planner=planner))
    assert caught.value.code == code
    assert len(attempts) == 1
