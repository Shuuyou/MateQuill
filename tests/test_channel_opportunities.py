"""Regional preparation never reads private content or crosses business scopes."""
import asyncio
from contextlib import contextmanager, asynccontextmanager
import time

import pytest
from fastapi import HTTPException

from app import channel_opportunities as service, channel_workflows as channel
from app.models import EvidenceBundle, Fact
from app.store_campaigns import StoreInput, StoreRepository
from app.tenancy import TenantContext, set_context, reset_context, save_body, list_resources
from app.tenancy.database import clear_engine_cache, db_connection, tenants, users

pytestmark = pytest.mark.real_tenancy


@pytest.fixture
def actors(tmp_path, monkeypatch):
    monkeypatch.setenv('MATEQUILL_DATABASE_URL', 'sqlite:///' + (tmp_path / 'tenant.sqlite3').as_posix())
    clear_engine_cache()
    with db_connection() as connection:
        for tenant in ('a', 'b'):
            connection.execute(tenants.insert().values(id=tenant, name=tenant, created_at=time.time()))
        for user in ('hq', 'sales', 'other'):
            connection.execute(users.insert().values(id=user, email=user+'@test.local', name=user,
                password_hash='not-a-login-hash', active=True, created_at=time.time()))

    @contextmanager
    def acting(user='hq', tenant='a', store_id=None, role='admin'):
        token = set_context(TenantContext(user_id=user, tenant_id=tenant, store_id=store_id,
            roles=(role,), store_ids=(store_id,) if store_id else (), all_stores=role == 'admin',
            grants=((role, None if role == 'admin' else store_id),)))
        try:
            yield
        finally:
            reset_context(token)
    yield acting
    clear_engine_cache()


def setup_campaign():
    stores = [StoreRepository().save_store(StoreInput(name=name)) for name in ('福雷德广场店', '办公商圈店')]
    campaign = channel.create_campaign(channel.CampaignPackageInput(title='新品体验', brief='围绕真实任务了解新品',
        product='HUAWEI Mate 80', platform='moments', store_ids=[store['id'] for store in stores]))
    channel.publish_campaign(campaign['id'])
    return stores, campaign, channel.list_assignments()


class Provider:
    def retrieve(self, product, query):
        return EvidenceBundle(product=product, facts=[Fact(id='fact:1', product=product, label='容量',
            value='128GB', quote='128GB', source_id='official')])


async def discover(query, profile):
    return {'state': 'found', 'items': [{'source_id': 'place:1', 'title': profile['name'],
        'url': 'https://example.com/location', 'excerpt': profile['name'] + '周边学校与办公楼'}]}


def plan(system, payload):
    name = payload['store']['name']
    return {'title': name+'的整理时刻', 'product': 'HUAWEI Mate 80', 'rationale': '研究到周边环境',
        'audience_hypothesis': name+'附近有整理照片需要的人', 'customer_task': '整理照片',
        'content_angle': name+'围绕照片整理展开', 'visual_direction': '真实照片与产品素材',
        'evidence_ids': ['place:1'], 'fact_ids': ['fact:1'], 'capture_steps': ['拍一张桌面照片'],
        'experience_steps': [{'seconds': 60, 'action': '查看存储容量', 'customer_value': '了解自己的需要'}],
        'materials': ['官方产品素材'], 'capabilities_to_confirm': ['展示机可操作'], 'next_action': '采用后准备图文'}


def prepare(campaign_id, **kwargs):
    return asyncio.run(service.prepare_campaign(campaign_id, planner=plan, provider=Provider(), discover=discover, **kwargs))


def test_headquarters_prepares_different_real_store_proposals_without_private_inputs(actors):
    with actors():
        stores, campaign, assignments = setup_campaign()
        save_body('agent_conversation', 'secret-chat', {'messages': ['PRIVATE_CUSTOMER_SECRET']},
            store_id=stores[0]['id'], visibility='private')
        observed = []
        def capture(system, payload):
            observed.append(str(payload))
            return plan(system, payload)
        result = asyncio.run(service.prepare_campaign(campaign['id'], planner=capture,
            provider=Provider(), discover=discover))
        assert all(row['status'] == 'prepared' for row in result['stores'])
        assert len({row['opportunity']['content_angle'] for row in result['stores']}) == 2
        assert 'PRIVATE_CUSTOMER_SECRET' not in ''.join(observed)
        assert len(list_resources(service.KIND)) == 2
        cached = prepare(campaign['id'])
        assert all(row['cache_hit'] for row in cached['preparations'])
        assert not list_resources(channel.PUBLICATION)


def test_cross_tenant_and_store_staff_cannot_prepare_regional_tasks(actors):
    with actors():
        stores, campaign, assignments = setup_campaign()
    with actors(tenant='b'):
        with pytest.raises(HTTPException) as error:
            prepare(campaign['id'])
        assert error.value.status_code == 404
    with actors(user='sales', store_id=stores[0]['id'], role='salesperson'):
        with pytest.raises(HTTPException) as error:
            prepare(campaign['id'])
        assert error.value.status_code == 403


def test_withdrawn_campaign_and_unassigned_store_do_not_research(actors):
    with actors():
        stores, campaign, assignments = setup_campaign()
        with pytest.raises(HTTPException) as error:
            prepare(campaign['id'], store_ids=['not-assigned'])
        assert error.value.status_code == 404
        channel.withdraw_campaign(campaign['id'])
        with pytest.raises(HTTPException) as error:
            prepare(campaign['id'])
        assert error.value.status_code == 409


def test_unknown_fact_or_ambiguous_place_does_not_create_shared_proposal(actors):
    with actors():
        _, campaign, _ = setup_campaign()
        def hallucination(system, payload):
            return {**plan(system, payload), 'fact_ids': ['invented']}
        result = asyncio.run(service.prepare_campaign(campaign['id'], planner=hallucination,
            provider=Provider(), discover=discover))
        assert all(item['status'] == 'needs_retry' for item in result['preparations'])
        assert all(row['body']['status'] == 'needs_retry' and not row['body'].get('plan')
            for row in list_resources(service.KIND))


def test_store_adoption_keeps_locked_task_and_queues_without_publication(actors, monkeypatch):
    with actors():
        stores, campaign, assignments = setup_campaign()
        prepare(campaign['id'])
    from app import store_autopilot
    dispatched = []
    async def enqueue(identifier, prepared, *, refresh=False):
        assert refresh is True
        channel.validate_generation_task(prepared['task'])
        dispatched.append(prepared)
        return {'status': 'queued', 'job_id': 'real-dispatch-boundary'}
    monkeypatch.setattr(store_autopilot, 'ensure_store_draft', enqueue)
    assignment = next(item for item in assignments if item['store_id'] == stores[0]['id'])
    other = next(item for item in assignments if item['store_id'] == stores[1]['id'])
    with actors(user='sales', store_id=stores[0]['id'], role='salesperson'):
        result = asyncio.run(service.adopt_opportunity(assignment['id']))
        assert result['draft']['status'] == 'queued'
        assert dispatched[0]['task']['product'] == 'HUAWEI Mate 80'
        assert dispatched[0]['task']['store_id'] == stores[0]['id']
        assert '本次选定事实ID：["fact:1"]' in dispatched[0]['task']['requirements']
        with pytest.raises(HTTPException) as error:
            asyncio.run(service.adopt_opportunity(other['id']))
        assert error.value.status_code == 404
        assert not list_resources(channel.PUBLICATION)


def test_ambiguous_store_is_visible_as_needing_location_and_cannot_be_adopted(actors):
    with actors():
        stores, campaign, assignments = setup_campaign()
        async def ambiguous(query, profile):
            return {'state': 'ambiguous', 'items': [], 'candidates': [{'city': '杭州'}, {'city': '其他城市'}]}
        result = asyncio.run(service.prepare_campaign(campaign['id'], planner=plan,
            provider=Provider(), discover=ambiguous))
        assert all(row['status'] == 'needs_location' for row in result['stores'])
    target = next(item for item in assignments if item['store_id'] == stores[0]['id'])
    with actors(user='sales', store_id=stores[0]['id'], role='salesperson'):
        assert service.assignment_opportunity(target['id'])['status'] == 'needs_location'
        with pytest.raises(HTTPException) as error:
            asyncio.run(service.adopt_opportunity(target['id']))
        assert error.value.status_code == 409


def test_campaign_withdrawal_during_planning_prevents_late_writes(actors):
    with actors():
        _, campaign, _ = setup_campaign()
        def withdraw(system, payload):
            channel.withdraw_campaign(campaign['id'])
            return plan(system, payload)
        result = asyncio.run(service.prepare_campaign(campaign['id'], planner=withdraw,
            provider=Provider(), discover=discover))
        assert not list_resources(service.KIND)
        assert all(not item['available'] for item in result['stores'])


def test_same_name_store_queries_include_public_city_and_strip_internal_suite(actors):
    with actors():
        first = StoreRepository().save_store(StoreInput(name='来福士中心店',
            location='杭州市上城区新业路228号L3-305铺位'))
        campaign = channel.create_campaign(channel.CampaignPackageInput(title='本地体验', brief='真实新品任务',
            product='HUAWEI Mate 80', store_ids=[first['id']]))
        channel.publish_campaign(campaign['id'])
        queries = []
        async def capture(query, profile):
            queries.append((query, profile))
            return {'state': 'found', 'items': [{'source_id': 'place:wrong-city',
                'title': '来福士中心 周边介绍(上海市)', 'url': 'https://example.com/shanghai',
                'excerpt': '另一个城市的同名商场'}]}
        result = asyncio.run(service.prepare_campaign(campaign['id'], planner=plan,
            provider=Provider(), discover=capture))
        assert queries and '杭州市' in queries[0][0] and '新业路228号' in queries[0][0]
        assert 'L3-305' not in queries[0][0] and '来福士中心店' not in queries[0][0]
        rows = list_resources(service.KIND)
        # The planner cannot cite the rejected different-city source.
        assert all(not (row['body'].get('place_context') or {}).get('sources') for row in rows)


def test_single_store_resume_retains_other_shared_directions(actors):
    with actors():
        stores, campaign, _ = setup_campaign()
        prepare(campaign['id'], store_ids=[stores[0]['id']])
        payloads = []
        def capture(system, payload):
            payloads.append(payload)
            return plan(system, payload)
        asyncio.run(service.prepare_campaign(campaign['id'], store_ids=[stores[1]['id']],
            planner=capture, provider=Provider(), discover=discover))
        assert payloads[0]['other_store_directions'] == [stores[0]['name']+'围绕照片整理展开']
        assert len(payloads[0]['assigned_stores']) == 2
        assert payloads[0]['schema']['properties']['evidence_ids']['minItems'] == 1


def test_ten_store_queue_starts_timeout_only_after_worker_is_available(actors, monkeypatch):
    with actors():
        stores = [StoreRepository().save_store(StoreInput(name=f'演示门店{index}')) for index in range(10)]
        campaign = channel.create_campaign(channel.CampaignPackageInput(title='十店体验', brief='真实新品任务',
            product='HUAWEI Mate 80', store_ids=[item['id'] for item in stores]))
        channel.publish_campaign(campaign['id'])
        async def verify():
            release, full = asyncio.Event(), asyncio.Event()
            entered = []
            @asynccontextmanager
            async def timer(seconds):
                entered.append(seconds)
                if len(entered) == 3:
                    full.set()
                yield
            class WaitingProvider(Provider):
                async def retrieve(self, product, query):
                    await release.wait()
                    return super().retrieve(product, query)
            monkeypatch.setattr(service.asyncio, 'timeout', timer)
            task = asyncio.create_task(service.prepare_campaign(campaign['id'], planner=plan,
                provider=WaitingProvider(), discover=discover))
            await asyncio.wait_for(full.wait(), timeout=5)
            assert entered == [120, 120, 120]
            release.set()
            result = await task
            assert len(entered) == 10
            assert all(item['status'] == 'prepared' for item in result['stores'])
        asyncio.run(verify())


def test_internal_candidates_are_selected_persisted_and_optional_choice_is_real(actors):
    with actors():
        stores, campaign, assignments = setup_campaign()
        prepare(campaign['id'], store_ids=[stores[0]['id']])
        def candidates(system, payload):
            base = plan(system, payload)
            return {'selected_id': 'repeat', 'candidates': [
                {'id': 'repeat', 'plan': {**base, 'rationale': '研究到周边环境'}},
                {'id': 'class', 'plan': {**base, 'title': '课间资料收在一起', 'customer_task': '课程资料整理',
                    'rationale': '教室课间收齐作业文件', 'content_angle': '把散落的课堂资料收在一起', 'visual_direction': '对照两组文件缩略图'}},
                {'id': 'trip', 'plan': {**base, 'title': '旅程资料提前归档', 'customer_task': '整理登机与差旅资料',
                    'rationale': '办公室出发前收齐行程文件', 'content_angle': '出发前把行程收在一起', 'visual_direction': '行程文件步骤分镜'}}]}
        result = asyncio.run(service.prepare_campaign(campaign['id'], store_ids=[stores[1]['id']],
            planner=candidates, provider=Provider(), discover=discover))
        current = next(row for row in result['stores'] if row['store_id'] == stores[1]['id'])
        assert len(current['alternatives']) == 3
        assert sum(candidate['selected'] for candidate in current['alternatives']) == 1
        assert current['diversity']['history_count'] == 1
        assignment = next(row for row in assignments if row['store_id'] == stores[1]['id'])
        changed = service.select_opportunity_candidate(assignment['id'], 'trip')
        chosen = next(row for row in changed['stores'] if row['store_id'] == stores[1]['id'])
        assert chosen['opportunity']['title'] == '旅程资料提前归档'
        assert chosen['diversity']['selection_mode'] == 'explicit'
        with pytest.raises(HTTPException) as error:
            service.select_opportunity_candidate(assignment['id'], 'invented')
        assert error.value.status_code == 404
    with actors(user='sales', store_id=stores[1]['id'], role='salesperson'):
        assert service.assignment_opportunity(assignment['id'])['opportunity']['title'] == '旅程资料提前归档'
        with pytest.raises(HTTPException) as error:
            service.select_opportunity_candidate(assignment['id'], 'class')
        assert error.value.status_code == 403
