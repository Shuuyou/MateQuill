"""Real sessions exercise legacy creation endpoints, not an authorization mock."""
import asyncio
import io
import time
from types import SimpleNamespace
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from PIL import Image
from sqlalchemy import select, update

from app.api import app
from app.tenancy import set_context, reset_context, save_body
from app.tenancy.auth import context_from_session, session_row
from app.tenancy.database import db_connection, memberships
from app.run_records import save_run
from scripts.tenant_admin import bootstrap

pytestmark = pytest.mark.real_tenancy
PASSWORD = 'Only-for-tests-2026-strong!'
ORIGIN = {'Origin': 'http://testserver'}


@pytest.fixture
def workspace(tmp_path, monkeypatch):
    monkeypatch.setenv('MATEQUILL_DATABASE_URL', 'sqlite:///' + (tmp_path / 'work.sqlite').as_posix())
    from app.tenancy.database import get_engine
    from app.generation_jobs import dispatch, usage
    first = bootstrap(email='hq-a@example.test', name='甲运营', tenant_name='甲企业', password=PASSWORD)
    second = bootstrap(email='hq-b@example.test', name='乙运营', tenant_name='乙企业', password=PASSWORD)
    # Avoid tests reading any developer uploads.
    monkeypatch.setattr('app.media_routes.ROOT', tmp_path)
    with TestClient(app) as client:
        login(client)
        store = client.post('/api/stores', json={'name': '校园店', 'audience': '大学生'}, headers=ORIGIN).json()
        response = client.post('/api/auth/workspace', json={'tenant_id': first['tenant_id'], 'store_id': store['id']}, headers=ORIGIN)
        assert response.status_code == 200, response.text
        yield client, first, second, store


def login(client, email='hq-a@example.test'):
    result = client.post('/api/auth/login', json={'email': email, 'password': PASSWORD}, headers=ORIGIN)
    assert result.status_code == 200, result.text


def context(client):
    return context_from_session(session_row(client.cookies.get('matequill_session')))


def create_record(client):
    identifier = uuid4().hex
    token = set_context(context(client))
    try:
        save_run(identifier, {'run_id': identifier, 'title': '本店的故事', 'status': 'ready',
            'blocks': [{'text': '来自本店实际场景', 'fact_ids': []}]})
    finally:
        reset_context(token)
    return identifier


def test_real_sessions_guard_legacy_run_export_and_library(workspace):
    client, first, second, store = workspace
    identifier = create_record(client)
    assert client.get('/api/runs/' + identifier).status_code == 200
    assert client.get(f'/api/runs/{identifier}/export?format=html').status_code == 200
    assert identifier in [row['id'] for row in client.get('/api/creations').json()['items']]
    login(client, 'hq-b@example.test')
    assert client.get('/api/runs/' + identifier).status_code == 404
    assert client.get(f'/api/runs/{identifier}/export?format=json').status_code == 404
    assert identifier not in [row['id'] for row in client.get('/api/creations').json()['items']]
    assert client.patch(f'/api/creations/run/{identifier}/sharing', json={'visibility': 'store'}, headers=ORIGIN).status_code == 404
    client.cookies.clear()
    assert client.get('/api/runs/' + identifier).status_code == 401


def test_uploaded_image_not_accessible_or_usable_by_other_tenant(workspace):
    client, *_ = workspace
    output = io.BytesIO()
    Image.new('RGB', (80, 60), 'white').save(output, 'JPEG')
    response = client.post('/api/media/intake', data={'kind': 'photo'},
        files={'file': ('store.jpg', output.getvalue(), 'image/jpeg')}, headers=ORIGIN)
    assert response.status_code == 200, response.text
    identifier = response.json()['id']
    assert client.get('/api/media/' + identifier).status_code == 200
    assert client.get('/api/assets/upload-' + identifier).status_code == 200
    login(client, 'hq-b@example.test')
    assert client.get('/api/media/' + identifier).status_code == 404
    assert client.get('/api/assets/upload-' + identifier).status_code == 404
    result = client.post('/api/run', json={'product': 'HUAWEI MateBook Pro', 'mode': 'demo', 'media_ids': [identifier]}, headers=ORIGIN)
    assert result.status_code == 404  # rejected before a model or retriever sees it


def test_poster_body_and_edit_enforce_the_same_scope(workspace):
    client, *_ = workspace
    from app.poster import PosterDocument
    from app.poster_routes import _save
    # Use a real poster created by the deterministic compositor.
    from app.poster import PosterBrief
    from app.vector_design import compose_vector_poster
    from app.models import TaskInput
    token = set_context(context(client))
    try:
        doc = compose_vector_poster(TaskInput(product='HUAWEI MateBook Pro', mode='demo'),
            {'title': '校园创作', 'blocks': [{'text': '真实的学习场景'}], 'assets': [], 'facts': [], 'status': 'draft'}, PosterBrief())
        _save(doc)
    finally:
        reset_context(token)
    assert client.get(f'/api/posters/{doc.id}').status_code == 200
    login(client, 'hq-b@example.test')
    for suffix in ('', '/preview.svg', '/export?format=json'):
        assert client.get(f'/api/posters/{doc.id}{suffix}').status_code == 404
    assert client.patch(f'/api/posters/{doc.id}', json={'edits': []}, headers=ORIGIN).status_code == 404


def test_product_imports_are_scoped_not_a_global_catalog(workspace):
    client, *_ = workspace
    from app.product_knowledge import ProductRepository
    token = set_context(context(client))
    try:
        repository = ProductRepository()
        draft = repository.create_draft({'name': '甲企业测试产品', 'facts': [{'label': '屏幕', 'value': 'test'}]})
        repository.confirm(draft['draft_id'])
        assert repository.list_products()[0]['name'] == '甲企业测试产品'
    finally:
        reset_context(token)
    login(client, 'hq-b@example.test')
    token = set_context(context(client))
    try:
        assert ProductRepository().list_products() == []
        from fastapi import HTTPException
        with pytest.raises(HTTPException) as error:
            ProductRepository().confirm(draft['draft_id'])
        assert error.value.status_code == 404
    finally:
        reset_context(token)


def test_jobs_are_durable_idempotent_and_deliver_authorized_results(workspace, monkeypatch):
    client, first, _, store = workspace
    async def fake_workflow(self, task):
        body = {'run_id': uuid4().hex, 'status': 'ready', 'title': '真实任务契约测试',
                'blocks': [{'text': '工作流替身，仅用于离线测试', 'fact_ids': []}], 'assets': [], 'sources': []}
        return SimpleNamespace(model_dump=lambda **kwargs: body)
    monkeypatch.setattr('app.workflow.AgentWorkflow.run', fake_workflow)
    payload = {'task': {'product': 'HUAWEI MateBook Pro', 'mode': 'demo', 'store_id': store['id']},
               'idempotency_key': 'test-job-idempotency'}
    first_job = client.post('/api/jobs', json=payload, headers=ORIGIN)
    assert first_job.status_code == 202, first_job.text
    identifier = first_job.json()['id']
    duplicate = client.post('/api/jobs', json=payload, headers=ORIGIN)
    assert duplicate.json()['id'] == identifier
    for _ in range(80):
        body = client.get('/api/jobs/' + identifier).json()
        if body['status'] in {'succeeded', 'failed'}:
            break
        time.sleep(.05)
    assert body['status'] == 'succeeded', body
    assert 'task' not in body
    assert client.get('/api/runs/' + body['result']['run_id']).status_code == 200
    assert client.get('/api/usage').json()['completed'] == 1
    login(client, 'hq-b@example.test')
    assert client.get('/api/jobs/' + identifier).status_code == 404


def test_budget_releases_failures_and_rejects_exhausted_tenant(workspace, monkeypatch):
    client, *_ = workspace
    from app.generation_jobs import generation_budget
    monkeypatch.setenv('MATEQUILL_DAILY_CREATIONS', '1')
    token = set_context(context(client))
    try:
        with pytest.raises(RuntimeError):
            with generation_budget():
                raise RuntimeError('simulated provider failure')
        with generation_budget():
            pass
        from fastapi import HTTPException
        with pytest.raises(HTTPException) as error:
            with generation_budget():
                pass
        assert error.value.status_code == 429
    finally:
        reset_context(token)


def test_revoked_queued_identity_is_cancelled_without_model_call(workspace, monkeypatch):
    client, *_ = workspace
    from app.generation_jobs import dispatch, execute_claimed, _claim
    identifier = uuid4().hex
    owner = context(client)
    token = set_context(owner)
    try:
        save_body('generation_job', identifier, {'id': identifier, 'status': 'queued', 'task': {'mode': 'demo'}})
        with db_connection() as connection:
            connection.execute(dispatch.insert().values(id=identifier, tenant_id=owner.tenant_id,
                user_id=owner.user_id, store_id=owner.store_id, session_hash=owner.session_hash,
                status='queued', lease_until=0, created_at=time.time(), idempotency_key=uuid4().hex))
            connection.execute(update(memberships).where(memberships.c.user_id == owner.user_id).values(active=False))
        called = []
        async def forbidden(*args, **kwargs):
            called.append(True)
            raise AssertionError('A revoked job must never call a model')
        monkeypatch.setattr('app.api.run_task', forbidden)
        row = _claim()
        if row:
            asyncio.run(execute_claimed(row))
        assert not called
        with db_connection() as connection:
            status = connection.execute(select(dispatch.c.status).where(dispatch.c.id == identifier)).scalar_one()
        assert status == 'cancelled'
    finally:
        reset_context(token)


def test_shared_run_grants_only_selected_photo_not_private_media_library(workspace):
    import hashlib
    client, _, _, store = workspace
    output = io.BytesIO()
    Image.new('RGB', (90, 60), '#ddd').save(output, 'JPEG')
    captures = []
    for _ in range(2):
        response = client.post('/api/media/intake', data={'kind': 'photo'},
            files={'file': ('photo.jpg', output.getvalue(), 'image/jpeg')}, headers=ORIGIN)
        assert response.status_code == 200
        captures.append(response.json()['id'])
    identifier = uuid4().hex
    aid = 'upload-' + captures[0]
    normalized = client.get('/api/media/' + captures[0]).content
    token = set_context(context(client))
    try:
        save_run(identifier, {'run_id': identifier, 'status': 'ready', 'title': '本店共享照片稿',
            'assets': [{'id': aid, 'url': '/api/assets/' + aid, 'sha256': hashlib.sha256(normalized).hexdigest()}],
            'images': [{'asset_id': aid}], 'blocks': [{'text': '门店真实场景'}],
            'creative_plan': {'cards': [{'headline': '今天的门店', 'body': '真实的故事', 'asset_ids': [aid]}]}})
    finally:
        reset_context(token)
    assert client.patch(f'/api/creations/run/{identifier}/sharing', json={'visibility': 'store'}, headers=ORIGIN).status_code == 200
    invitation = client.post('/api/team/invitations', json={'email': 'peer@example.test',
        'role': 'salesperson', 'store_id': store['id']}, headers=ORIGIN).json()
    assert client.post('/api/auth/accept-invitation', json={'token': invitation['token'],
        'name': '同店同事', 'password': PASSWORD}, headers=ORIGIN).status_code == 200
    assert client.get(f'/api/runs/{identifier}').status_code == 200
    assert client.get(f'/api/runs/{identifier}/assets/{aid}').status_code == 200
    assert client.get('/api/media/' + captures[0]).status_code == 404
    assert client.get('/api/media/' + captures[1]).status_code == 404
    assert client.get(f'/api/runs/{identifier}/assets/upload-{captures[1]}').status_code == 404
    html = client.get(f'/api/runs/{identifier}/export?format=html')
    assert html.status_code == 200 and 'data:image/jpeg;base64,' in html.text
