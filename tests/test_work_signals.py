import asyncio
from dataclasses import replace
import time
import pytest
from fastapi import HTTPException
from app import work_signals as signals
from app.tenancy import TenantContext, set_context, reset_context, register_resource, service_update_body
from app.tenancy.database import db_connection, tenants, users, memberships
from app.gtm_agent.tools import AgentTools
from app.gtm_agent.jobs import enqueue_turn
from app.gtm_agent.models import TurnInput
from app.gtm_agent.repository import create_conversation
from app.tenancy.auth import new_session, context_from_session, session_row
from app.models import EvidenceBundle, Fact, Source

pytestmark = pytest.mark.real_tenancy


@pytest.fixture
def scoped(tmp_path, monkeypatch):
    monkeypatch.setenv('MATEQUILL_DATABASE_URL', 'sqlite:///' + (tmp_path/'signals.sqlite').as_posix())
    with db_connection() as conn:
        conn.execute(tenants.insert(), [{'id':'a','name':'A','created_at':1},{'id':'b','name':'B','created_at':1}])
        conn.execute(users.insert(), [{'id':u,'name':u,'email':u+'@example.test','password_hash':'test','active':True,'created_at':1} for u in ('u','v')])
        conn.execute(memberships.insert().values(id='membership-u',user_id='u',tenant_id='a',store_id=None,role='operator',active=True,created_at=1))
    context = context_from_session(session_row(new_session('u','a','s1')))
    token = set_context(context)
    register_resource('store','s1',body={'name':'门店','location':''},visibility='store')
    try:
        yield context
    finally:
        reset_context(token)


def source(identifier='t1', text='顾客问怎么传照片，预算五千。', visibility='private'):
    register_resource('agent_turn',identifier,body={'id':identifier,'text':text,'effects':{}},visibility=visibility)
    return signals.capture('agent_turn',identifier,text,audience='group' if visibility=='store' else 'private')


def test_signal_redaction_and_exact_source(scoped):
    original='顾客问怎么传照片，联系13812345678，邮箱person@example.com，密码：do-not-store。'
    value=source(text=original)
    assert '13812345678' not in value['text'] and 'person@example.com' not in value['text']
    assert 'do-not-store' not in value['text']
    assert '顾客问怎么传照片' in value['text']
    with pytest.raises(HTTPException):signals.capture('agent_turn','t1','编造的新问题')


def test_query_time_brief_preserves_words_and_rejects_fabricated_budget(scoped):
    value=source()
    brief=signals.prepare_brief([{'signal_id':value['id'],'field':'budget','value':'五千','quote':'预算五千'}],turn_id='t1')
    assert brief['customer_stage']=='unknown' and brief['product_evidence'] is False
    assert '五千' in signals.brief_requirements(brief)
    with pytest.raises(HTTPException):
        signals.prepare_brief([{'signal_id':value['id'],'field':'budget','value':'一万','quote':'预算五千'}],turn_id='t1')
    with pytest.raises(ValueError):
        signals.prepare_brief([{'signal_id':value['id'],'field':'customer_stage','value':'意向','quote':'预算五千'}],turn_id='t1')


def test_withdraw_expiry_and_source_update_are_rechecked(scoped):
    value=source()
    brief=signals.prepare_brief([{'signal_id':value['id'],'field':'question','value':'怎么传照片','quote':'顾客问怎么传照片'}],turn_id='t1')
    assert signals.relevant('传照片')
    assert signals.get_signal(value['id'],now=value['expires_at']-1)
    with pytest.raises(HTTPException):signals.get_signal(value['id'],now=value['expires_at'])
    signals.withdraw(value['id'])
    assert signals.relevant('传照片')==[]
    with pytest.raises(HTTPException):signals.brief_requirements(brief)
    assert source()['status']=='withdrawn'  # A transport retry cannot resurrect it.
    newer=source('t2','顾客问接口。')
    service_update_body('agent_turn','t2',{'text':'原话已撤回'})
    with pytest.raises(HTTPException):signals.get_signal(newer['id'])


def test_managers_and_groups_do_not_inherit_other_private_signals(scoped):
    private=source()
    shared=source('t2','这段话已经在本店群里提供','store')
    token=set_context(replace(scoped,user_id='v'))
    try:
        assert [r['id'] for r in signals.relevant()] == [shared['id']]
        with pytest.raises(HTTPException):signals.get_signal(private['id'])
        assert [r['id'] for r in signals.relevant(audience='group')] == [shared['id']]
        token2=set_context(replace(scoped,store_id='s2'))
        try:assert signals.relevant()==[]
        finally:reset_context(token2)
        token2=set_context(replace(scoped,tenant_id='b'))
        try:assert signals.relevant()==[]
        finally:reset_context(token2)
    finally:reset_context(token)


def test_existing_tools_put_grounded_brief_in_real_generation_task(scoped):
    value=source()
    conversation={'id':'c','audience':'private','messages':[]}
    turn={'id':'t1','text':'根据顾客的问题写朋友圈','observations':[]}
    captured=[]
    class Provider:
        def catalog(self):return [{'name':'HUAWEI MateBook Pro'}]
        def retrieve(self,product,query):return EvidenceBundle(product=product,
            facts=[Fact(id='f1',product=product,label='外观',value='绿色',quote='绿色',source_id='s1')],
            sources=[Source(id='s1',title='产品资料')])
    async def generate(task):
        captured.append(task)
        register_resource('run','r1',body={'status':'ready','title':'传照片'})
        return {'status':'ready','run_id':'r1','title':'传照片'}
    async def run():
        tools=AgentTools(conversation,turn,provider=Provider(),generator=generate)
        history=await tools.execute('read_history',{})
        assert history['work_signals'][0]['id']==value['id']
        await tools.execute('prepare_work_brief',{'brief_fields':[{'signal_id':value['id'],
            'field':'question','value':'怎么传照片','quote':'顾客问怎么传照片'}]})
        await tools.execute('read_catalog',{})
        await tools.execute('read_product',{'product':'HUAWEI MateBook Pro'})
        result=await tools.execute('generate_content',{'product':'HUAWEI MateBook Pro',
            'audience':'对照片传输有疑问的人','scene':'解释照片传输','platform':'moments'})
        assert result['artifact']['id']=='r1'
        assert '怎么传照片' in captured[0].requirements
        with pytest.raises(HTTPException):await tools.execute('withdraw_work_signal',{'key':value['id']})
    asyncio.run(run())


def test_web_and_im_shared_enqueue_captures_user_but_not_automatic_task(scoped):
    from uuid import uuid4
    conversation=create_conversation()
    payload=TurnInput(text='刚才顾客问照片怎么传到电脑',client_turn_id=uuid4().hex)
    turn=enqueue_turn(conversation['id'],payload)
    assert signals.relevant()[0]['source_id']==turn['id']
    assert enqueue_turn(conversation['id'],payload)['id']==turn['id']
    assert len(signals.relevant())==1
    automatic=create_conversation()
    auto_turn=enqueue_turn(automatic['id'],TurnInput(text='内部自动准备任务，不能当店员原话',client_turn_id=uuid4().hex),origin='daily_store_agent')
    assert len(signals.relevant())==1
    assert signals.capture('agent_turn',auto_turn['id'],'内部自动准备任务，不能当店员原话') is None


def test_daily_policy_prevents_image_spend_and_false_staff_memory(scoped):
    tools=AgentTools({'id':'c','audience':'private'}, {'id':'t','text':'自然表达',
        'execution_policy':{'source':'daily_store','allow_new_images':False,'allow_memory_writes':False}})
    async def run():
        for name in ('create_design_bundle','create_poster','revise_design','remember'):
            with pytest.raises(HTTPException):await tools.execute(name,{})
        assert await tools.execute('read_memory',{})=={'memories':[]}
    asyncio.run(run())
