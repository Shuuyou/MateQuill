"""Automatic design context preserves the boundary between records and hypotheses."""
from copy import deepcopy
from types import SimpleNamespace

from fastapi import HTTPException
import pytest

from app import design_store_context as context_module
from app.gtm_agent.place_context import build_place_context, place_requirements
from app.models import TaskInput


@pytest.fixture
def records(monkeypatch):
    ctx = SimpleNamespace(store_id='store-a', tenant_id='tenant-a', user_id='alice', is_legacy=False)
    task = TaskInput(store_id='store-a', product='HUAWEI Example', audience='本次考虑通勤的读者',
                     scene='整理明天通勤要带的物件', goal='让读者带着具体问题了解产品')
    source = {'run_id': 'source-run', 'title': '今天接待了十位高收入顾客',
              'blocks': [{'text': '本店真实顾客都是学生'}], '_poster_task': task.model_dump(), 'assets': []}
    store = {'kind': 'store', 'id': 'store-a', 'store_id': 'store-a', 'updated_at': 10,
             'body': {'name': '杭州示例华为店', 'location': '杭州示例路10号',
                      'audience': '配置里的内容方向', 'core_tasks': ['课堂演示'],
                      'voice': '自然、具体', 'internal_api_key': 'SECRET_CANARY',
                      'materials': ['/private/store-secret.jpg']}}
    resources = {('store', 'store-a'): store,
                 ('run', 'source-run'): {'kind': 'run', 'id': 'source-run',
                    'store_id': 'store-a', 'body': source}}
    rows = {'store_opportunity': []}
    memory = {'entries': []}
    assignments = {}
    def authorize(kind, identifier):
        if (kind, identifier) not in resources:
            raise HTTPException(404, 'not found')
        return deepcopy(resources[kind, identifier])
    def assignment(identifier):
        if identifier not in assignments:
            raise HTTPException(410, 'revoked')
        return deepcopy(assignments[identifier])
    monkeypatch.setattr(context_module, 'require_context', lambda: ctx)
    monkeypatch.setattr(context_module, 'authorize_resource', authorize)
    monkeypatch.setattr(context_module, 'list_resources', lambda kind: deepcopy(rows[kind]))
    monkeypatch.setattr(context_module, 'collect_store_memory', lambda *args, **kwargs: deepcopy(memory))
    from app import channel_workflows, public_signals, place_discovery
    monkeypatch.setattr(channel_workflows, '_assignment', assignment)
    monkeypatch.setattr(public_signals, 'search_public_signals', lambda *args: pytest.fail('no network search'))
    monkeypatch.setattr(place_discovery, 'discover_place', lambda *args, **kwargs: pytest.fail('no second research'))
    return SimpleNamespace(ctx=ctx, task=task, source=source, store=store,
        resources=resources, rows=rows, memory=memory, assignments=assignments)


def collect(r, **kwargs):
    return context_module.collect_design_store_context(r.task, r.source, **kwargs)


def local_requirements():
    researches = [{'state': 'found', 'items': [{'source_id': 'nearby-1',
        'url': 'https://www.example.com/store-location', 'title': '本店周边公开介绍',
        'excerpt': '示例路附近有办公园区和图书馆。', 'observed_at': '2026-10-22'}]}]
    context = build_place_context(researches, selection={'location_status': 'resolved',
        'location_summary': '位于示例路附近', 'audience': '这次写给下班后整理资料的人',
        'scene': '下班前把手机里的零散资料整理到电脑',
        'visual_direction': '手机、电脑与随身本子共同构成一个收尾动作',
        'evidence_ids': ['nearby-1']})
    return place_requirements(context)


def add_opportunity(r):
    row = {'kind': 'store_opportunity', 'id': 'research-cache', 'store_id': 'store-a',
        'body': {'assignment_id': 'assignment-a', 'campaign_version': 2, 'prepared_at': 20,
          'result': {'status': 'ready', 'task': {'product': r.task.product},
            'opportunity': {'title': '一个具体的现场任务', 'audience': '过去选择的内容读者',
                'scene': '一次演示前整理素材', 'visual_idea': '一个设备间的动作'},
            'sources': [{'source_id': 'signal:1', 'kind': 'public_signal',
                'url': 'https://www.example.com/news', 'excerpt': '周边公共场馆的开放活动'},
                {'source_id': 'private', 'kind': 'store_profile', 'data': 'SECRET_CANARY'}]}}}
    r.rows['store_opportunity'].append(row)
    r.assignments['assignment-a'] = ({}, {'store_id': 'store-a'}, {'version': 2})
    return row


def test_store_identity_is_grounded_and_history_copy_is_never_customer_fact(records):
    r = records
    result = collect(r)
    assert result['store']['name'] == '杭州示例华为店'
    assert result['store']['location'] == '杭州示例路10号'
    assert result['reader_task'] == r.task.scene
    assert result['service_angle'] == r.task.scene
    assert result['service_capabilities'] == []  # content task is not an actual service capability
    assert result['core_tasks'] == ['课堂演示']
    assert result['context_mode'] == 'authorized_store_context'
    assert {item['basis'] for item in result['audience_hypotheses']} == {
        'editorial_hypothesis', 'store_profile_context'}
    serialized = str(result)
    assert all(value not in serialized for value in ('十位高收入顾客', '真实顾客都是学生',
                                                   'SECRET_CANARY', '/private/store-secret'))


def test_canonical_place_envelope_supplies_real_signals_and_reader_action(records):
    r = records
    r.task = r.task.model_copy(update={'requirements': local_requirements()})
    result = collect(r)
    assert result['reader_task'] == '下班前把手机里的零散资料整理到电脑'
    assert result['context_mode'] == 'grounded_local_context'
    assert result['audience_hypotheses'][0]['basis'] == 'editorial_hypothesis'
    assert result['audience_hypotheses'][0]['evidence_ids'] == ['local:nearby-1']
    assert result['local_signals'][0]['text'] == '示例路附近有办公园区和图书馆。'
    assert result['store']['location'] == r.store['body']['location']
    evidence = {item['id']: item for item in result['evidence']}
    assert evidence['local:nearby-1']['url'] == 'https://www.example.com/store-location'
    assert result['creative_opportunities'][0]['visual_idea'].startswith('手机、电脑')


def test_forged_or_ambiguous_place_decision_does_not_become_grounded_context(records):
    r = records
    for requirements in (local_requirements().replace('"nearby-1"]', '"unknown-source"]'),
                         local_requirements().replace('"state": "found"', '"state": "ambiguous"')):
        r.task = r.task.model_copy(update={'requirements': requirements})
        result = collect(r)
        assert result['local_signals'] == []
        assert result['reader_task'] == r.task.scene
        assert result['context_mode'] == 'authorized_store_context'


def test_cached_opportunity_revalidates_assignment_and_campaign_version(records):
    r = records
    row = add_opportunity(r)
    assert collect(r)['local_signals'][0]['text'] == '周边公共场馆的开放活动'
    assert collect(r)['reader_task'] == r.task.scene  # old cache does not overwrite an explicit current task
    r.assignments['assignment-a'] = ({}, {'store_id': 'store-a'}, {'version': 3})
    assert collect(r)['local_signals'] == []
    r.assignments.clear()
    result = collect(r)
    assert result['local_signals'] == []
    assert not any(item['kind'] == 'cached_editorial_proposal' for item in result['creative_opportunities'])
    assert row['body']['result']['opportunity']['audience'] not in str(result)


def test_wrong_store_product_and_assignment_caches_are_excluded(records):
    r = records
    row = add_opportunity(r)
    row['store_id'] = 'other-store'
    assert collect(r)['local_signals'] == []
    row['store_id'] = 'store-a'
    row['body']['result']['task']['product'] = 'Other product'
    assert collect(r)['local_signals'] == []
    row['body']['result']['task']['product'] = r.task.product
    r.task = r.task.model_copy(update={'assignment_id': 'different-assignment'})
    assert collect(r)['local_signals'] == []


def test_only_task_authorized_current_store_photos_are_exposed(records):
    r = records
    r.task = r.task.model_copy(update={'media_ids': ['own-photo', 'shared-foreign', 'revoked', 'audio']})
    source_assets = [
        {'id': 'upload-' + identifier, 'provenance': 'store_upload', 'title': '一张现场照片'}
        for identifier in r.task.media_ids]
    r.source['assets'] = source_assets
    assets = {item['id']: {**item, 'data_url': 'PRIVATE_RASTER', 'local_path': '/private/file.png'} for item in source_assets}
    r.resources['media', 'own-photo'] = {'kind': 'media', 'id': 'own-photo', 'store_id': 'store-a',
        'body': {'kind': 'photo', 'observations': {'caption': '桌面上的真机与展示牌'}}}
    r.resources['media', 'shared-foreign'] = {'kind': 'media', 'id': 'shared-foreign', 'store_id': 'other-store',
        'body': {'kind': 'photo', 'observations': {'caption': '其他门店的照片'}}}
    r.resources['media', 'audio'] = {'kind': 'media', 'id': 'audio', 'store_id': 'store-a', 'body': {'kind': 'audio'}}
    result = collect(r, assets=assets)
    assert result['photo_asset_ids'] == ['upload-own-photo']
    assert result['context_mode'] == 'store_photo_and_grounded_context'
    assert result['photo_observations'][0]['basis'] == 'photo_model_observation'
    assert all(value not in str(result) for value in ('其他门店的照片', 'PRIVATE_RASTER', '/private/file.png'))
    del r.resources['media', 'own-photo']
    assert collect(r, assets=assets)['photo_asset_ids'] == []


def test_caller_supplied_source_assets_cannot_replace_authorized_run(records):
    r = records
    forged = {**deepcopy(r.source), 'assets': [{'id': 'upload-forged', 'provenance': 'store_upload'}]}
    result = context_module.collect_design_store_context(r.task, forged,
        assets={'upload-forged': {'provenance': 'store_upload'}})
    assert result['photo_asset_ids'] == []


def test_memory_remains_traceable_preferences_and_content_hypotheses(records):
    r = records
    r.memory['entries'] = [
        {'id': 'f', 'kind': 'workflow_preference', 'text': '少一点字，保留现场感', 'event': 'edited',
         'sources': [{'kind': 'strategy_feedback', 'id': 'feedback-a', 'field': 'note', 'provenance': 'app_event'}]},
        {'id': 'a', 'kind': 'content_hypothesis', 'text': '之前选过校园场景',
         'sources': [{'kind': 'run', 'id': 'source-run', 'field': 'task.scene'}]},
        {'id': 'v', 'kind': 'material_observation', 'text': '照片里看见一张展示桌'}]
    result = collect(r)
    assert result['workflow_preferences'][0]['event'] == 'edited'
    assert result['creative_opportunities'][0]['kind'] == 'historical_content_hypothesis'
    evidence = {item['id']: item for item in result['evidence']}
    assert evidence['memory:f']['source_records'][0]['id'] == 'feedback-a'
    assert evidence['memory:v']['kind'] == 'material_observation'
    assert result['photo_asset_ids'] == []  # a caption in history does not confer access to a photo
    r.memory['entries'] = []
    assert not collect(r)['workflow_preferences']


def test_operational_service_declarations_are_separate_from_content_configuration(records):
    r = records
    r.store['body']['service_capabilities'] = [
        {'name': '预约真机讲解', 'status': 'available'}, {'name': '旧的促销', 'status': 'expired'}, '基础使用指导']
    result = collect(r)
    assert [item['name'] for item in result['service_capabilities']] == ['预约真机讲解', '基础使用指导']
    assert result['service_angle'] == '预约真机讲解；基础使用指导'
    assert all(item['basis'] == 'authorized_operational_record' for item in result['service_capabilities'])
    assert '旧的促销' not in str(result)


def test_contacts_are_redacted_and_metadata_is_allowlisted(records):
    r = records
    r.store['body']['voice'] = '联系 13812345678 secret@example.com ' + '字' * 3000
    result = collect(r, limit=1000)
    assert '13812345678' not in str(result) and 'secret@example.com' not in str(result)
    assert len(result['voice']) <= 220 and len(result['evidence']) <= 60
    assert not any('api_key' in key for key in result)


def test_wrong_active_store_rejected_before_resource_reads(records, monkeypatch):
    r = records
    monkeypatch.setattr(context_module, 'authorize_resource', lambda *args: pytest.fail('scope before reads'))
    r.task = r.task.model_copy(update={'store_id': 'other-store'})
    with pytest.raises(HTTPException) as error:
        collect(r)
    assert error.value.status_code == 409


def test_readable_foreign_store_run_cannot_describe_this_store(records):
    r = records
    r.resources['run', 'source-run']['store_id'] = 'other-store'
    with pytest.raises(HTTPException) as error:
        collect(r)
    assert error.value.status_code == 409

@pytest.mark.real_tenancy
def test_real_tenant_and_private_run_boundaries_apply_to_derived_design_context(monkeypatch, tmp_path):
    from app.tenancy import TenantContext, set_context, reset_context, register_resource
    from app.tenancy.database import db_connection, tenants, users
    monkeypatch.setenv('MATEQUILL_DATABASE_URL', 'sqlite:///' + (tmp_path / 'design-context.sqlite3').as_posix())
    with db_connection() as connection:
        connection.execute(tenants.insert(), [{'id': 'tenant', 'name': 'Tenant', 'created_at': 1},
            {'id': 'foreign', 'name': 'Foreign', 'created_at': 1}])
        connection.execute(users.insert(), [{'id': user, 'email': user + '@example.com', 'name': user,
            'password_hash': 'x', 'active': True, 'created_at': 1} for user in ('alice', 'bob')])
    def identity(user, tenant='tenant'):
        return TenantContext(user_id=user, tenant_id=tenant, store_id='store',
                             store_ids=('store',), roles=('salesperson',))
    token = set_context(identity('alice'))
    try:
        register_resource('store', 'store', 'store', 'store', {'name': '本店', 'location': '示例路'})
        alice_task = TaskInput(store_id='store', product='P', scene='Alice私有创作任务')
        register_resource('run', 'alice-run', 'store', 'private', {'run_id': 'alice-run',
            '_poster_task': alice_task.model_dump()})
        set_context(identity('bob'))
        bob_task = TaskInput(store_id='store', product='P', scene='Bob当前任务')
        bob_source = {'run_id': 'bob-run', '_poster_task': bob_task.model_dump()}
        register_resource('run', 'bob-run', 'store', 'private', bob_source)
        result = context_module.collect_design_store_context(bob_task, bob_source)
        assert result['store']['name'] == '本店'
        assert 'Alice私有' not in str(result)
        assert result['reader_task'] == 'Bob当前任务'
        with pytest.raises(HTTPException) as private:
            context_module.collect_design_store_context(bob_task, {'run_id': 'alice-run'})
        assert private.value.status_code == 404
        set_context(identity('bob', 'foreign'))
        with pytest.raises(HTTPException) as foreign:
            context_module.collect_design_store_context(bob_task, bob_source)
        assert foreign.value.status_code == 404
    finally:
        reset_context(token)


def test_unrelated_product_history_is_excluded_but_style_preferences_cross_products(records):
    r = records
    r.resources['run', 'headphone-run'] = {'kind': 'run', 'id': 'headphone-run', 'store_id': 'store-a',
        'body': {'task': {'product': 'HUAWEI FreeBuds Neo', 'scene': '下班走出写字楼戴上耳机'}}}
    r.memory['entries'] = [
        {'id': 'headphones', 'kind': 'content_hypothesis', 'text': '下班走出写字楼戴上耳机',
         'sources': [{'kind': 'run', 'id': 'headphone-run', 'field': 'task.scene'}]},
        {'id': 'style', 'kind': 'workflow_preference', 'text': '标题少一点字', 'event': 'edited',
         'sources': [{'kind': 'run', 'id': 'headphone-run', 'field': 'feedback.note'}]},
        {'id': 'matched', 'kind': 'content_hypothesis', 'text': '本产品曾做过的观察主题',
         'sources': [{'kind': 'run', 'id': 'source-run', 'field': 'task.scene'}]}]
    result = collect(r)
    assert '下班走出写字楼戴上耳机' not in str(result)
    assert 'memory:headphones' not in str(result)
    assert result['workflow_preferences'][0]['text'] == '标题少一点字'
    assert [item['reader_task'] for item in result['creative_opportunities']] == ['本产品曾做过的观察主题']


def test_ecosystem_history_matches_only_explicit_products_and_keeps_authorized_source_links(records):
    r = records
    r.task = r.task.model_copy(update={'ecosystem_products': ['HUAWEI FreeBuds Neo']})
    r.resources['run', 'headphone-run'] = {'kind': 'run', 'id': 'headphone-run', 'store_id': 'store-a',
        'body': {'task': {'product': 'huawei freebuds neo'}}}
    r.resources['run', 'unrelated'] = {'kind': 'run', 'id': 'unrelated', 'store_id': 'store-a',
        'body': {'task': {'product': 'Other tablet'}}}
    r.memory['entries'] = [{'id': 'ecosystem', 'kind': 'content_hypothesis', 'text': '当前生态产品的使用主题',
        'sources': [{'kind': 'run', 'id': 'headphone-run', 'field': 'task.scene'},
                    {'kind': 'run', 'id': 'unrelated', 'field': 'task.scene'},
                    {'kind': 'run', 'id': 'revoked', 'field': 'task.scene'}]}]
    result = collect(r)
    assert result['creative_opportunities'][0]['reader_task'] == '当前生态产品的使用主题'
    entry = next(item for item in result['evidence'] if item['id'] == 'memory:ecosystem')
    assert [item['id'] for item in entry['source_records']] == ['headphone-run']
    del r.resources['run', 'headphone-run']
    assert 'memory:ecosystem' not in str(collect(r))


def test_history_source_ecosystem_can_ground_current_product_but_product_name_prefix_cannot(records):
    r = records
    r.resources['run', 'ecosystem-run'] = {'kind': 'run', 'id': 'ecosystem-run', 'store_id': 'store-a',
        'body': {'_poster_task': {'product': 'Companion', 'ecosystem_products': [r.task.product]}}}
    r.resources['run', 'different-version'] = {'kind': 'run', 'id': 'different-version', 'store_id': 'store-a',
        'body': {'task': {'product': r.task.product + ' Pro'}}}
    r.memory['entries'] = [
        {'id': 'ecosystem', 'kind': 'content_hypothesis', 'text': '显式包含当前产品的生态任务',
         'sources': [{'kind': 'run', 'id': 'ecosystem-run'}]},
        {'id': 'different-version', 'kind': 'content_hypothesis', 'text': '其他版本的历史场景',
         'sources': [{'kind': 'run', 'id': 'different-version'}]},
        {'id': 'no-provenance', 'kind': 'content_hypothesis', 'text': '没有可授权源记录的场景'}]
    result = collect(r)
    assert [item['reader_task'] for item in result['creative_opportunities']] == ['显式包含当前产品的生态任务']
    assert '其他版本的历史场景' not in str(result)
    assert '没有可授权源记录的场景' not in str(result)
