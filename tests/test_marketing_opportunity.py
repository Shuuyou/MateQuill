"""Plans must bind real research and product facts to executable store work."""
import asyncio

import pytest
from fastapi import HTTPException

from app.gtm_agent.marketing_opportunity import validate_plan, get_opportunity
from app.gtm_agent.repository import get_conversation
from app.gtm_agent.tools import AgentTools
from app.tenancy import register_resource
from test_gtm_agent_core import identities, acting, start, Provider, scripted

pytestmark = pytest.mark.real_tenancy


def plan():
    return dict(title='下课后把资料接着整理', product='HUAWEI MateBook Pro',
        rationale='研究来源显示附近有学习空间，准备跨设备资料体验',
        audience_hypothesis='附近正在整理课程资料的同学', customer_task='课后把手机资料接到电脑继续整理',
        content_angle='下课了，资料不用重新传一遍', visual_direction='桌面实拍手机与电脑共同处理资料',
        evidence_ids=['place-campus'], fact_ids=['f1'], capture_steps=['拍摄手机与电脑的实物关系'],
        experience_steps=[dict(seconds=60, action='先确认演示设备和功能条件，再展示资料协同', customer_value='看懂自己的整理流程')],
        materials=['具备相应功能和登录条件的演示设备'], capabilities_to_confirm=['门店是否有对应演示设备'],
        next_action='店员确认设备后，准备一条真实演示图文')


def research():
    return dict(source_id='place-campus', title='附近学习空间',
        url='https://public.example/campus', excerpt='周边有高校学习空间')


def test_plan_validates_fact_sources_and_duration():
    bundle = Provider().retrieve('HUAWEI MateBook Pro', '')
    kwargs = dict(place_context={'sources': [research()]}, product_evidence={bundle.product: bundle})
    assert validate_plan(plan(), **kwargs).experience_steps[0].seconds == 60
    for update in ({'evidence_ids': ['invented']}, {'fact_ids': ['invented']},
                   {'experience_steps': [dict(seconds=120, action='展示', customer_value='体验')]*2}):
        with pytest.raises(ValueError):
            validate_plan({**plan(), **update}, **kwargs)


def test_plan_persists_and_actual_run_is_attached(identities):
    owner = identities[0]
    conversation, turn = start(owner, '给海棠广场店准备图文和体验方案')
    captured = []

    async def generator(task):
        captured.append(task)
        register_resource('run', 'opportunity-content', body={'status':'ready', 'title':'下课以后'})
        return {'status':'ready','run_id':'opportunity-content','title':'下课以后'}

    async def execute():
        tools = AgentTools(conversation, {**turn, 'text':'给海棠广场店准备图文和体验方案'},
            provider=Provider(), search=lambda query: [research()], generator=generator)
        await tools.execute('research_store', {'query':'海棠广场 地址'})
        await tools.execute('ground_store', {'grounding':dict(location_status='resolved',
            location_summary='附近学习空间', audience='附近同学', scene='整理资料',
            visual_direction='桌面', evidence_ids=['place-campus'])})
        await tools.execute('read_product', {'product':'HUAWEI MateBook Pro'})
        prepared = await tools.execute('prepare_opportunity', {'opportunity':plan()})
        assert prepared['opportunity']['experience_status'] == 'proposed'
        result = await tools.execute('generate_content', dict(product='HUAWEI MateBook Pro',
            audience='通用人群', scene='普通使用', platform='moments'))
        return prepared, result

    with acting(owner):
        prepared, result = asyncio.run(execute())
        saved = get_conversation(conversation['id'])['current_opportunity']
        assert saved['id'] == prepared['opportunity']['id']
        assert saved['artifacts'][0]['id'] == result['artifact']['id']
        assert captured[0].scene == plan()['customer_task']
        assert '门店是否有对应演示设备' in captured[0].requirements
    with acting(identities[1]):
        with pytest.raises(HTTPException) as exc:
            get_opportunity(prepared['opportunity']['id'])
        assert exc.value.status_code == 404


def test_opportunity_only_turn_finishes_without_claiming_content(identities):
    from app.gtm_agent.runtime import run_turn
    owner = identities[0]
    conversation, turn = start(owner, '先准备一个体验机会方案')
    proposal = {**plan(), 'evidence_ids': [], 'rationale': '尚无区位信息，作为可调整的编辑提案'}
    planner = scripted(
        {'action':'tool','tool':'read_product','arguments':{'product':'HUAWEI MateBook Pro'}},
        {'action':'tool','tool':'prepare_opportunity','arguments':{'opportunity':proposal}},
        {'action':'reply'})
    with acting(owner):
        outcome = asyncio.run(run_turn(turn['id'], planner=planner,
            tools_factory=lambda conversation, turn: AgentTools(conversation, turn, provider=Provider())))
        assert outcome['status'] == 'completed'
        assert outcome['artifact'] is None
        saved = get_conversation(conversation['id'])['current_opportunity']
        assert saved['status'] == 'prepared'
        assert saved['artifacts'] == []


def test_reviewed_design_reselection_binds_actual_new_run_and_keeps_experience_task(identities, monkeypatch):
    from app.gtm_agent.effects import checkpoint_generated_artifact
    from app.gtm_agent.repository import artifact_reference
    from app.gtm_agent.marketing_opportunity import attach_artifact
    from app.models import TaskInput
    from app.tenancy import authorize_resource, service_update_body
    owner = identities[0]
    conversation, turn = start(owner, '准备这家店的图文和体验方案')
    turn = {**turn, 'text':'准备这家店的图文和体验方案'}
    calls = []
    task = TaskInput(product='HUAWEI MateBook Pro', audience='原来的人群', scene='原来的任务',
                     platform='moments', store_id=owner['context'].store_id)

    async def fake_design(parameters):
        calls.append(('design', parameters['source_run_id']))
        if parameters['source_run_id'] == 'original-run':
            raise HTTPException(422, {'code':'marketing_reselect', 'feedback':['换一个切角']})
        body = {'id':parameters['design_id'], 'title':'新稿配图', 'status':'ready', 'version':1,
                'platform':'moments', 'source_run_id':parameters['source_run_id'], 'artboards':[]}
        register_resource('design', body['id'], body=body)
        checkpoint_generated_artifact('design', body['id'], body)
        return body

    async def fake_rewrite(value, request):
        calls.append(('text', value.scene))
        assert value.scene == plan()['customer_task']
        assert value.audience == plan()['audience_hypothesis']
        body = {'run_id':'replacement-run', 'status':'ready', 'title':'同一任务的新切角',
                '_poster_task':value.model_dump()}
        register_resource('run', body['run_id'], body=body)
        checkpoint_generated_artifact('run', body['run_id'], body)
        return body

    monkeypatch.setattr('app.design_service.execute_design', fake_design)
    monkeypatch.setattr('app.api.run_task', fake_rewrite)
    with acting(owner):
        register_resource('run', 'original-run', body={'status':'ready', 'title':'原稿',
                                                     '_poster_task':task.model_dump()})
        turn['reply_to_artifact'] = artifact_reference('run', 'original-run')
        tools = AgentTools(conversation, turn, provider=Provider())
        asyncio.run(tools.execute('read_product', {'product':task.product}))
        prepared = asyncio.run(tools.execute('prepare_opportunity', {
            'opportunity':{**plan(), 'evidence_ids':[]}}))['opportunity']
        attach_artifact(prepared['id'], turn['reply_to_artifact'])
        result = asyncio.run(tools.execute('create_design_bundle', {'platform':'moments'}))
        assert result['source_run_id'] == 'replacement-run'
        saved = get_opportunity(prepared['id'])
        assert [(a['kind'],a['id']) for a in saved['artifacts']][:2] == [
            ('run','original-run'), ('run','replacement-run')]
        assert saved['artifacts'][-1]['id'] == result['artifact']['id']
        assert saved['customer_task'] == plan()['customer_task']
        # Simulate a crash after commit but before projection was attached.
        row = authorize_resource('marketing_opportunity', prepared['id'])
        service_update_body(row['kind'], row['id'], {**row['body'],
            'artifacts':[turn['reply_to_artifact']]}, expected_version=row['version'])
        restored = AgentTools(get_conversation(conversation['id']), turn, provider=Provider())
        reused = asyncio.run(restored.execute('create_design_bundle', {'platform':'moments'}))
        assert reused['reused'] is True
        assert reused['source_run_id'] == 'replacement-run'
        assert len(calls) == 3  # no repeat paid image or text request
        assert get_opportunity(prepared['id'])['artifacts'][-2]['id'] == 'replacement-run'


@pytest.mark.parametrize('resume', [False, True])
def test_structured_image_delivery_keeps_saved_text_and_finishes_design(identities, resume):
    from app.gtm_agent.runtime import run_turn
    from app.gtm_agent.repository import update_turn
    owner = identities[0]
    conversation, turn = start(owner, '准备完整图文')
    calls = []
    run = {'kind':'run', 'id':'delivery-text', 'title':'已保存的文案', 'version':1}

    class DeliveryTools:
        opportunity = {'deliverables':['content','images'], 'artifacts':[]}
        def __init__(self, *_): pass
        async def execute(self, name, arguments):
            calls.append(name)
            if name == 'generate_content':
                return {'artifact':run}
            assert name == 'create_design_bundle'
            return {'artifact':{'kind':'design','id':'delivery-images','title':'图文作品'}}

    decisions = ([] if resume else [{'action':'tool','tool':'generate_content'}]) + [
        {'action':'reply'},  # A premature completion must not hide missing images.
        {'action':'tool','tool':'generate_content'},  # Never regenerate committed text.
        {'action':'tool','tool':'create_design_bundle'}]
    with acting(owner):
        if resume:
            register_resource('run', run['id'], body={'status':'ready','title':run['title']})
            update_turn(turn['id'], lambda body: {**body,'artifact':run, 'delivery_scope':['content','images']})
        result = asyncio.run(run_turn(turn['id'], planner=scripted(*decisions), tools_factory=DeliveryTools))
        assert result['status'] == 'completed'
        assert result['artifact']['kind'] == 'design'
        assert calls == (['create_design_bundle'] if resume else ['generate_content','create_design_bundle'])
