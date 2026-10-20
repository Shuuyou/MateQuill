import json
import sqlite3
from concurrent.futures import ThreadPoolExecutor

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import ValidationError

from app.campaign_routes import router
from app.models import TaskInput
from app.store_campaigns import CampaignInput, StoreInput, StoreRepository, plan_campaign


@pytest.fixture
def repository(tmp_path):
    return StoreRepository(tmp_path / 'stores.sqlite3')


def make_stores(repository):
    return [repository.save_store(StoreInput(name=name, audience=audience, core_tasks=[task]))
            for name, audience, task in [
                ('校园店', '大一新生与家长', '入学选电脑'),
                ('商务店', '常出差的职场人', '会议资料整理'),
                ('创作店', '设计专业学生', '课程交稿'),
            ]]


def test_empty_repository_has_no_invented_stores(repository):
    assert repository.list_stores() == []
    plan = plan_campaign(CampaignInput(brief='新品推广', product='指定新品'), [])
    assert plan['status'] == 'needs_input'
    assert plan['variants'] == []


def test_store_survives_reopen_and_update(repository):
    store = repository.save_store(StoreInput(name='真实门店', audience='学生', core_tasks=['选电脑']))
    reopened = StoreRepository(repository.path)
    updated = reopened.save_store(StoreInput(name='更新门店', audience='家长'), store['id'])
    assert updated['created_at'] == store['created_at']
    assert StoreRepository(repository.path).list_stores()[0]['name'] == '更新门店'
    with pytest.raises(KeyError):
        repository.save_store(StoreInput(name='测试', audience='学生'), 'nonexistent')


def test_campaign_has_distinct_editorial_directions_and_shared_product(repository):
    stores = make_stores(repository)
    request = CampaignInput(brief='讲清这款新品适合怎样的任务', product='HUAWEI MateBook Pro',
                            store_ids=[store['id'] for store in stores])
    plan = plan_campaign(request, stores)
    assert plan['status'] == 'ready'
    assert plan['diversity']['distinct_directions'] == 3
    assert plan['diversity']['minimum_pairwise_changed_dimensions'] >= 3
    assert len({v['angle'] for v in plan['variants']}) == 3
    assert len({v['narrative_device'] for v in plan['variants']}) == 3
    assert len({v['visual_direction'] for v in plan['variants']}) == 3
    for variant in plan['variants']:
        task = TaskInput.model_validate(variant['task'])
        assert task.product == request.product
        assert task.audience == variant['audience']
        assert variant['customer_task'] in variant['angle']
        assert '核对' not in variant['angle']
        assert json.loads(task.requirements)['angle'] == variant['angle']
    repository.save_campaign(plan)
    assert StoreRepository(repository.path).get_campaign(plan['campaign_id']) == plan


def test_no_new_facts_or_personal_testimonial_are_added(repository):
    store = repository.save_store(StoreInput(name='用户填写的店', audience='学生',
                                             location='杭州', materials=['产品陈列照片']))
    request = CampaignInput(brief='新品上市', product='未检索的新品', store_ids=[store['id']])
    variant = plan_campaign(request, [store])['variants'][0]
    instructions = json.loads(variant['task']['requirements'])
    assert instructions['store_profile_user_supplied']['location'] == '杭州'
    assert '产品信息逐项使用本次检索到的证据' in ''.join(instructions['writing_rules'])
    for invented in ('附近大学', '到店实测', '已经卖出', '立省', '性能提升', '顾客说'):
        assert invented not in json.dumps(variant, ensure_ascii=False)


def test_unknown_store_and_duplicate_ids(repository):
    store = make_stores(repository)[0]
    with pytest.raises(KeyError):
        plan_campaign(CampaignInput(brief='推广', product='新品', store_ids=['unknown']), [store])
    result = plan_campaign(CampaignInput(brief='推广', product='新品', store_ids=[store['id']] * 2), [store])
    assert len(result['variants']) == 1


def test_same_brief_preserves_explainable_editorial_choices(repository):
    stores = make_stores(repository)
    request = CampaignInput(brief='同一新品活动', product='指定型号', store_ids=[s['id'] for s in stores])
    first = plan_campaign(request, stores)
    second = plan_campaign(request, stores)
    keys = ('audience', 'customer_task', 'angle', 'narrative_device', 'visual_direction', 'why', 'direction_ids')
    assert [{k: v[k] for k in keys} for v in first['variants']] == [
        {k: v[k] for k in keys} for v in second['variants']]
    assert first['campaign_id'] != second['campaign_id']
    assert first['diversity'] == second['diversity']


def test_foreign_keys_and_campaign_atomicity(repository):
    bad = {'campaign_id': 'test', 'variants': [{'id': 'v', 'store_id': 'nonexistent'}]}
    with pytest.raises(sqlite3.IntegrityError):
        repository.save_campaign(bad)
    with pytest.raises(KeyError):
        repository.get_campaign('test')


def test_concurrent_first_use_migration_is_safe(tmp_path):
    path = tmp_path / 'parallel.sqlite3'
    with ThreadPoolExecutor(max_workers=6) as executor:
        results = list(executor.map(lambda _: StoreRepository(path).list_stores(), range(6)))
    assert results == [[]] * 6


def test_blank_and_oversized_profile_rejected():
    for payload in ({'name': ' ', 'audience': '学生'},
                    {'name': '门店', 'audience': '学生', 'materials': ['x' * 501]}):
        with pytest.raises(ValidationError):
            StoreInput(**payload)


def test_campaign_rejects_more_companion_devices_than_generation_supports():
    with pytest.raises(ValidationError):
        CampaignInput(brief='门店任务', product='主产品', ecosystem_products=['设备'+str(index) for index in range(5)])


def test_http_create_plan_update_and_missing_store(monkeypatch, tmp_path):
    monkeypatch.setenv('MATEQUILL_STORE_DB', str(tmp_path / 'api.sqlite3'))
    app = FastAPI()
    app.include_router(router)
    with TestClient(app) as client:
        assert client.get('/api/stores').json() == {'stores': []}
        response = client.post('/api/stores', json={'name': '本店', 'audience': '大学生'})
        assert response.status_code == 201
        store = response.json()
        body = {'brief': '推广新品', 'product': '指定型号', 'store_ids': [store['id']]}
        plan = client.post('/api/campaigns/plan', json=body).json()
        assert plan['status'] == 'ready'
        assert client.get('/api/campaigns/' + plan['campaign_id']).json() == plan
        assert client.put('/api/stores/' + store['id'], json={'name': '本店', 'audience': '家长'}).status_code == 200
        assert client.post('/api/campaigns/plan', json={**body, 'store_ids': ['missing']}).status_code == 404
