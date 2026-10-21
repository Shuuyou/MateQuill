"""Business authorization and versioned channel execution tests."""
from contextlib import contextmanager
import time

import pytest
from fastapi import HTTPException, FastAPI
from fastapi.testclient import TestClient

from app import channel_workflows as flow
from app.channel_routes import router
from app.store_campaigns import StoreInput, StoreRepository
from app.tenancy import TenantContext, save_body, load_body, set_context, reset_context
from app.tenancy.database import clear_engine_cache, db_connection, tenants, users

pytestmark = pytest.mark.real_tenancy


@pytest.fixture
def actors(tmp_path, monkeypatch):
    monkeypatch.setenv('MATEQUILL_DATABASE_URL', 'sqlite:///' + (tmp_path / 'tenant.sqlite3').as_posix())
    clear_engine_cache()
    with db_connection() as connection:
        for tenant in ['company-a', 'company-b']:
            connection.execute(tenants.insert().values(id=tenant, name=tenant, created_at=time.time()))
        for user in ['hq-a', 'hq-b', 'sales-a', 'manager-a', 'sales-other']:
            connection.execute(users.insert().values(id=user, email=user+'@example.test', name=user,
                password_hash='fixture-not-a-login-hash', active=True, created_at=time.time()))

    @contextmanager
    def acting(user='hq-a', tenant='company-a', role='admin', stores=(), selected=None):
        scope = None if role in {'admin', 'operator'} else selected
        context = TenantContext(user_id=user, tenant_id=tenant, store_id=selected, roles=(role,),
            store_ids=tuple(stores), all_stores=scope is None, grants=((role, scope),))
        token = set_context(context)
        try:
            yield context
        finally:
            reset_context(token)
    yield acting
    clear_engine_cache()


def store(name='校园店'):
    return StoreRepository().save_store(StoreInput(name=name, audience='大学生', core_tasks=['课堂展示']))


def package(stores, **extras):
    return flow.create_campaign(flow.CampaignPackageInput(title='生态体验周', brief='从课堂准备讲生态',
        product='HUAWEI MateBook Pro', store_ids=[s['id'] for s in stores], **extras))


def ready_run(identifier, store_id, *, title='本店稿件', product='HUAWEI MateBook Pro', status='ready'):
    body = {'run_id': identifier, 'status': status, 'content': {'title': title, 'body': [{'text': '实际内容'}]},
        '_poster_task': {'product': product, 'platform': 'xiaohongshu', 'store_id': store_id}}
    save_body('run', identifier, body, store_id=store_id)
    return body


def test_store_business_registry_is_tenant_scoped_and_unknown_id_denied(actors):
    with actors():
        first = store()
    with actors('hq-b', 'company-b'):
        second = store('商务店')
        assert [s['id'] for s in StoreRepository().list_stores()] == [second['id']]
        with pytest.raises(HTTPException) as exc:
            StoreRepository().save_store(StoreInput(name='越权', audience='学生'), first['id'])
        assert exc.value.status_code == 404
    with actors():
        assert [s['id'] for s in StoreRepository().list_stores()] == [first['id']]


def test_salesperson_cannot_create_or_rewrite_store(actors):
    with actors():
        first = store()
    with actors('sales-a', role='salesperson', stores=[first['id']], selected=first['id']):
        for identifier in [None, first['id']]:
            with pytest.raises(HTTPException) as exc:
                StoreRepository().save_store(StoreInput(name='非法更新', audience='学生'), identifier)
            assert exc.value.status_code == 403


def test_campaign_publish_is_idempotent_and_tasks_include_real_store_scope(actors):
    with actors():
        first, second = store(), store('商务店')
        campaign = package([first, second])
        assert campaign['status'] == 'draft'
        flow.publish_campaign(campaign['id'])
        flow.publish_campaign(campaign['id'])
        assignments = flow.list_assignments()
        assert len(assignments) == 2
        for assignment in assignments:
            result = flow.assignment_task(assignment['id'])
            assert result['task']['store_id'] == assignment['store_id']
            assert result['campaign_version'] == 1
            assert result['task']['product'] == 'HUAWEI MateBook Pro'


def test_store_and_tenant_boundaries_cover_listing_and_direct_ids(actors):
    with actors():
        first, second = store(), store('隔壁店')
        campaign = package([first, second])
        flow.publish_campaign(campaign['id'])
        assignments = flow.list_assignments()
        other = next(a for a in assignments if a['store_id'] == second['id'])
    with actors('sales-a', role='salesperson', stores=[first['id']], selected=first['id']):
        assert len(flow.list_assignments()) == 1
        with pytest.raises(HTTPException) as exc:
            flow.assignment_task(other['id'])
        assert exc.value.status_code == 404
        with pytest.raises(HTTPException):
            package([first])
    with actors('hq-b', 'company-b'):
        assert flow.list_campaigns() == []
        assert flow.list_assignments() == []
        with pytest.raises(HTTPException) as exc:
            flow.publish_campaign(campaign['id'])
        assert exc.value.status_code == 404


def test_sales_submit_manager_review_publish_and_audit(actors):
    with actors():
        first = store()
        campaign = package([first])
        flow.publish_campaign(campaign['id'])
        assignment = flow.list_assignments()[0]
    scope = {'stores': [first['id']], 'selected': first['id']}
    with actors('sales-a', role='salesperson', **scope):
        flow.accept_assignment(assignment['id'])
        ready_run('run-1', first['id'])
        submitted = flow.submit_content(flow.SubmissionInput(assignment_id=assignment['id'], run_id='run-1'))
        assert submitted['revision'] == 1
        assert flow.submit_content(flow.SubmissionInput(assignment_id=assignment['id'], run_id='run-1'))['id'] == submitted['id']
        with pytest.raises(HTTPException) as exc:
            flow.review_submission(submitted['id'], flow.ReviewInput(decision='approved'))
        assert exc.value.status_code == 403
        with pytest.raises(HTTPException):
            flow.publish_submission(submitted['id'], flow.PublicationInput())
    with actors('manager-a', role='store_manager', **scope):
        assert flow.list_submissions()[0]['content_snapshot']['content']['title'] == '本店稿件'
        flow.review_submission(submitted['id'], flow.ReviewInput(decision='approved', note='可用于本店'))
    with actors('sales-a', role='salesperson', **scope):
        published = flow.publish_submission(submitted['id'], flow.PublicationInput(url='https://example.test/post/1'))
        assert published['status'] == 'published'
        assert published['publication']['source'] == 'manual'
        assert flow.list_assignments()[0]['status'] == 'published'
        assert flow.publish_submission(submitted['id'], flow.PublicationInput())['publication']['id'] == published['publication']['id']


def test_wrong_store_product_and_unready_run_cannot_be_submitted(actors):
    with actors():
        first, second = store(), store('其他门店')
        campaign = package([first])
        flow.publish_campaign(campaign['id'])
        assignment = flow.list_assignments()[0]
        for identifier, store_id, kwargs in [('wrong-store', second['id'], {}),
                ('wrong-product', first['id'], {'product': '另一产品'}),
                ('not-ready', first['id'], {'status': 'blocked'})]:
            ready_run(identifier, store_id, **kwargs)
            with pytest.raises(HTTPException):
                flow.submit_content(flow.SubmissionInput(assignment_id=assignment['id'], run_id=identifier))


def test_new_revision_invalidates_old_approval_and_old_content(actors):
    with actors():
        first = store()
        campaign = package([first])
        flow.publish_campaign(campaign['id'])
        assignment = flow.list_assignments()[0]
        ready_run('original', first['id'])
        original = flow.submit_content(flow.SubmissionInput(assignment_id=assignment['id'], run_id='original'))
        flow.review_submission(original['id'], flow.ReviewInput(decision='approved'))
        ready_run('updated', first['id'], title='修改后的版本')
        updated = flow.submit_content(flow.SubmissionInput(assignment_id=assignment['id'], run_id='updated'))
        assert updated['revision'] == 2 and updated['status'] == 'submitted'
        with pytest.raises(HTTPException) as exc:
            flow.publish_submission(original['id'], flow.PublicationInput())
        assert exc.value.status_code == 409
        with pytest.raises(HTTPException):
            flow.publish_submission(updated['id'], flow.PublicationInput())


def test_edited_run_requires_new_review_even_if_run_id_unchanged(actors):
    with actors():
        first = store()
        campaign = package([first])
        flow.publish_campaign(campaign['id'])
        assignment = flow.list_assignments()[0]
        ready_run('same-id', first['id'])
        submitted = flow.submit_content(flow.SubmissionInput(assignment_id=assignment['id'], run_id='same-id'))
        flow.review_submission(submitted['id'], flow.ReviewInput(decision='approved'))
        ready_run('same-id', first['id'], title='另一个内容')
        with pytest.raises(HTTPException) as exc:
            flow.publish_submission(submitted['id'], flow.PublicationInput())
        assert exc.value.status_code == 409


def test_campaign_version_change_blocks_old_assignment_and_new_publish_creates_latest(actors):
    with actors():
        first = store()
        campaign = package([first])
        flow.publish_campaign(campaign['id'])
        original = flow.list_assignments()[0]
        updated = flow.update_campaign(campaign['id'], flow.CampaignPackageInput(title='第二版', brief='更新任务',
            product='HUAWEI MateBook Pro', store_ids=[first['id']]))
        assert updated['version'] == 2
        with pytest.raises(HTTPException):
            flow.assignment_task(original['id'])
        flow.publish_campaign(campaign['id'])
        newest = next(row for row in flow.list_assignments() if row['campaign_version'] == 2)
        assert flow.assignment_task(newest['id'])['campaign_version'] == 2


def test_partner_grant_shares_package_without_source_private_drafts(actors):
    with actors():
        first = store()
        campaign = package([first])
        flow.publish_campaign(campaign['id'])
        ready_run('source-private', first['id'])
        grant = flow.create_grant(campaign['id'], flow.GrantInput(recipient_tenant_id='company-b'))
    with actors('hq-b', 'company-b'):
        second = store('伙伴门店')
        assert flow.list_campaigns() == []
        imported = flow.accept_grant(flow.AcceptGrantInput(token=grant['token'], store_ids=[second['id']]))
        assert imported['source_grant_id'] == grant['id']
        assert imported['store_ids'] == [second['id']]
        assignment = flow.list_assignments()[0]
        assert flow.assignment_task(assignment['id'])['task']['store_id'] == second['id']
        with pytest.raises(HTTPException):
            load_body('run', 'source-private')
        with pytest.raises(HTTPException):
            flow.create_grant(imported['id'], flow.GrantInput(recipient_tenant_id='company-a'))
        assert 'token' not in flow.list_grants()[0]
    with actors():
        assert all(row['store_id'] == first['id'] for row in flow.list_assignments())
        flow.revoke_grant(grant['id'])
    with actors('hq-b', 'company-b'):
        with pytest.raises(HTTPException) as exc:
            flow.assignment_task(assignment['id'])
        assert exc.value.status_code == 409


def test_grant_wrong_recipient_and_expired_token_cannot_be_accepted(actors):
    with actors():
        first = store()
        campaign = package([first])
        flow.publish_campaign(campaign['id'])
        grant = flow.create_grant(campaign['id'], flow.GrantInput(recipient_tenant_id='company-b'))
        with pytest.raises(HTTPException):
            flow.accept_grant(flow.AcceptGrantInput(token=grant['token'], store_ids=[first['id']]))
        with db_connection() as connection:
            connection.execute(flow.channel_grants.update().where(flow.channel_grants.c.id == grant['id']).values(expires_at=0))
    with actors('hq-b', 'company-b'):
        second = store()
        with pytest.raises(HTTPException):
            flow.accept_grant(flow.AcceptGrantInput(token=grant['token'], store_ids=[second['id']]))


def test_withdraw_publisher_package_revokes_partner_regeneration(actors):
    with actors():
        first = store()
        campaign = package([first])
        flow.publish_campaign(campaign['id'])
        grant = flow.create_grant(campaign['id'], flow.GrantInput(recipient_tenant_id='company-b'))
    with actors('hq-b', 'company-b'):
        second = store()
        flow.accept_grant(flow.AcceptGrantInput(token=grant['token'], store_ids=[second['id']]))
        assignment = flow.list_assignments()[0]
    with actors():
        flow.withdraw_campaign(campaign['id'])
    with actors('hq-b', 'company-b'):
        with pytest.raises(HTTPException):
            flow.assignment_task(assignment['id'])


def test_publication_rejects_non_web_link():
    from pydantic import ValidationError
    with pytest.raises(ValidationError):
        flow.PublicationInput(url='javascript:alert(1)')


def test_channel_http_direct_id_isolation_and_request_validation(actors):
    app = FastAPI()
    app.include_router(router)
    with actors():
        first = store()
        with TestClient(app) as client:
            response = client.post('/api/channel/campaigns', json={'title': 'HTTP活动', 'brief': '新品',
                'product': 'HUAWEI MateBook Pro', 'store_ids': [first['id']]})
            assert response.status_code == 201
            identifier = response.json()['id']
            assert client.post('/api/channel/campaigns/'+identifier+'/publish').status_code == 200
    with actors('hq-b', 'company-b'):
        with TestClient(app) as client:
            assert client.get('/api/channel/campaigns').json()['campaigns'] == []
            assert client.post('/api/channel/campaigns/'+identifier+'/publish').status_code == 404
            assert client.post('/api/channel/campaigns', json={'title':' ', 'brief':'x', 'product':'x'}).status_code == 422


def test_hq_selected_store_does_not_narrow_enterprise_campaign(actors):
    with actors():
        first, second = store(), store('商务店')
    with actors(selected=first['id']):
        campaign = package([first, second])
        flow.publish_campaign(campaign['id'])
        assert len(flow.list_assignments()) == 2
    with actors('sales-other', role='salesperson', stores=[second['id']], selected=second['id']):
        packages = flow.list_campaigns()
        assert len(packages) == 1
        assert packages[0]['store_ids'] == [second['id']]
        assert packages[0]['assignments_count'] == 1
        assignment = flow.list_assignments()[0]
        assert flow.assignment_task(assignment['id'])['task']['store_id'] == second['id']
        assert flow.accept_assignment(assignment['id'])['claimed_by'] == 'sales-other'


def test_group_campaign_plans_structural_diversity_for_similar_stores(actors):
    import json
    with actors():
        stores = [store('校园店一'), store('校园店二'), store('校园店三')]
        campaign = package(stores)
        flow.publish_campaign(campaign['id'])
        tasks = [json.loads(row['task']['requirements']) for row in flow.list_assignments()]
        assert len({task['narrative_device'] for task in tasks}) == 3
        assert len({task['visual_direction'] for task in tasks}) == 3


def test_cached_generation_task_is_revalidated_and_local_edits_preserve_campaign(actors):
    with actors():
        first = store()
        campaign = package([first])
        flow.publish_campaign(campaign['id'])
        assignment = flow.list_assignments()[0]
        task = flow.assignment_task(assignment['id'])['task']
        flow.validate_generation_task(task)
        flow.validate_generation_task({**task, 'requirements': task['requirements'] + '\n本店希望短一些'})
        for changes in [{'store_id': 'other-store'}, {'product': '另一产品'}, {'requirements': '替换总部资料'}]:
            with pytest.raises(HTTPException):
                flow.validate_generation_task({**task, **changes})
        flow.withdraw_campaign(campaign['id'])
        assert flow.list_assignments()[0]['available'] is False
        with pytest.raises(HTTPException):
            flow.validate_generation_task(task)


def test_submission_transaction_rolls_back_when_assignment_changed(actors, monkeypatch):
    from sqlalchemy import select
    from app.tenancy.database import resources
    with actors():
        first = store()
        campaign = package([first])
        flow.publish_campaign(campaign['id'])
        assignment = flow.list_assignments()[0]
        ready_run('atomic', first['id'])
        original = flow.service_update_body
        def conflict(kind, identifier, body, **kwargs):
            if kind == flow.ASSIGNMENT:
                raise HTTPException(409, 'Concurrent update')
            return original(kind, identifier, body, **kwargs)
        monkeypatch.setattr(flow, 'service_update_body', conflict)
        with pytest.raises(HTTPException):
            flow.submit_content(flow.SubmissionInput(assignment_id=assignment['id'], run_id='atomic'))
        with db_connection() as connection:
            assert connection.execute(select(resources.c.id).where(resources.c.kind == flow.SUBMISSION)).first() is None


def test_required_brand_copy_must_be_in_published_text_not_trace(actors):
    with actors():
        first = store()
        campaign = package([first], required_copy='欢迎到店体验')
        flow.publish_campaign(campaign['id'])
        assignment = flow.list_assignments()[0]
        body = ready_run('brand', first['id'])
        body['trace'] = [{'detail': '欢迎到店体验'}]
        save_body('run', 'brand', body, store_id=first['id'])
        with pytest.raises(HTTPException) as exc:
            flow.submit_content(flow.SubmissionInput(assignment_id=assignment['id'], run_id='brand'))
        assert exc.value.status_code == 422
        body['blocks'] = [{'text': '欢迎到店体验'}]
        save_body('run', 'brand', body, store_id=first['id'])
        assert flow.submit_content(flow.SubmissionInput(assignment_id=assignment['id'], run_id='brand'))['status'] == 'submitted'

def test_review_projection_reads_only_selected_private_photo_with_matching_hash(actors, monkeypatch, tmp_path):
    import hashlib
    import io
    from PIL import Image
    from app.media_routes import media_store
    from app.tenancy import authorize_resource
    monkeypatch.setattr('app.media_routes.ROOT', tmp_path)
    output = io.BytesIO()
    Image.new('RGB', (32, 32), 'white').save(output, 'JPEG')
    raw = output.getvalue()
    with actors():
        first, second = store(), store('其他门店')
        campaign = package([first])
        flow.publish_campaign(campaign['id'])
        assignment = flow.list_assignments()[0]
    scope = {'stores': [first['id']], 'selected': first['id']}
    with actors('sales-a', role='salesperson', **scope):
        physical = media_store()
        selected = physical.put(raw, 'image/jpeg', '.jpg')
        unused = physical.put(raw, 'image/jpeg', '.jpg')
        body = ready_run('photo-review', first['id'])
        body['images'] = [{'asset_id': 'upload-'+selected, 'caption': '本店实拍'}]
        body['assets'] = [{'id': 'upload-'+identifier, 'sha256': hashlib.sha256(raw).hexdigest()} for identifier in (selected, unused)]
        save_body('run', 'photo-review', body, store_id=first['id'])
        submitted = flow.submit_content(flow.SubmissionInput(assignment_id=assignment['id'], run_id='photo-review'))
    with actors('manager-a', role='reviewer', **scope):
        with pytest.raises(HTTPException):
            media_store().get(selected)  # still private outside the submission
        data, mime = flow.submission_asset(submitted['id'], 'upload-'+selected)
        assert data == raw and mime == 'image/jpeg'
        preview = flow.list_submissions()[0]['image_previews'][0]
        assert preview['url'].endswith('/assets/upload-'+selected)
        with pytest.raises(HTTPException) as exc:
            flow.submission_asset(submitted['id'], 'upload-'+unused)
        assert exc.value.status_code == 404
        application = FastAPI()
        application.include_router(router)
        with TestClient(application) as client:
            response = client.get(preview['url'])
            assert response.status_code == 200 and response.content == raw
            assert response.headers['cache-control'] == 'private, no-store'
        path = physical.root / (selected + '.jpg')
        path.write_bytes(b'changed-bytes')
        with pytest.raises(HTTPException) as changed:
            flow.submission_asset(submitted['id'], 'upload-'+selected)
        assert changed.value.status_code == 409
    with actors('sales-other', role='salesperson', stores=[second['id']], selected=second['id']):
        with pytest.raises(HTTPException) as other_store:
            flow.submission_asset(submitted['id'], 'upload-'+selected)
        assert other_store.value.status_code == 404
    with actors('hq-b', 'company-b'):
        with pytest.raises(HTTPException) as other_tenant:
            flow.submission_asset(submitted['id'], 'upload-'+selected)
        assert other_tenant.value.status_code == 404


def test_review_projection_rejects_photo_registry_from_another_store(actors, monkeypatch, tmp_path):
    import hashlib
    from app.media_routes import media_store
    monkeypatch.setattr('app.media_routes.ROOT', tmp_path)
    with actors():
        first, second = store(), store('另一门店')
        campaign = package([first])
        flow.publish_campaign(campaign['id'])
        assignment = flow.list_assignments()[0]
    with actors(selected=second['id']):
        media = media_store().put(b'another-store-file', 'image/jpeg', '.jpg')
    with actors(selected=first['id']):
        body = ready_run('bad-review-reference', first['id'])
        body['images'] = [{'asset_id': 'upload-'+media}]
        body['assets'] = [{'id': 'upload-'+media, 'sha256': hashlib.sha256(b'another-store-file').hexdigest()}]
        save_body('run', 'bad-review-reference', body, store_id=first['id'])
        submitted = flow.submit_content(flow.SubmissionInput(assignment_id=assignment['id'], run_id='bad-review-reference'))
        with pytest.raises(HTTPException) as error:
            flow.submission_asset(submitted['id'], 'upload-'+media)
        assert error.value.status_code == 404
