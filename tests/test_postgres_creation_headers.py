"""Exercise PostgreSQL JSONB header projection through the authenticated API."""
from uuid import uuid4

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from test_tenancy_postgres import pg_database
from app.resource_routes import router
from app.store_campaigns import StoreInput, StoreRepository
from app.tenancy import TenantContext, install_tenancy, save_body, set_context, reset_context
from app.tenancy.auth import new_session
from app.tenancy.resources import list_resource_headers

pytestmark = pytest.mark.real_tenancy


def test_postgres_creation_history_projects_headers_and_keeps_tenant_isolation(pg_database):
    first, second, suffix = pg_database
    context = TenantContext(first['user_id'], first['tenant_id'], roles=('admin',),
        all_stores=True, grants=(('admin', None),))
    token = set_context(context)
    try:
        store = StoreRepository().save_store(StoreInput(name='PG历史门店', audience='实际业务人员'))
        run_id, poster_id = uuid4().hex, uuid4().hex
        save_body('run', run_id, {'run_id': run_id, 'title': 'PG中文成稿标题', 'status': 'ready',
            'private_payload': 'never-transfer-this-body' * 40000}, store_id=store['id'])
        save_body('poster', poster_id, {'id': poster_id, 'status': 'draft',
            'assets': {'embedded': {'data_url': 'data:image/png;base64,' + 'A' * 1000000}}}, store_id=store['id'])
        headers = list_resource_headers('run')
        row = next(row for row in headers if row['id'] == run_id)
        assert row['title'] == 'PG中文成稿标题' and row['status'] == 'ready'
        assert 'body' not in row and 'private_payload' not in row
    finally:
        reset_context(token)
    application = FastAPI()
    install_tenancy(application)
    application.include_router(router)
    with TestClient(application) as client:
        client.cookies.set('matequill_session', new_session(first['user_id'], first['tenant_id'], store['id']))
        response = client.get('/api/creations')
        assert response.status_code == 200
        rows = response.json()['items']
        assert {row['id'] for row in rows} == {run_id, poster_id}
        assert next(row for row in rows if row['id'] == run_id)['title'] == 'PG中文成稿标题'
        assert next(row for row in rows if row['id'] == poster_id)['title'] == '海报设计'
        assert len(response.content) < 5000
        assert b'data:image/png' not in response.content
        client.cookies.clear()
        client.cookies.set('matequill_session', new_session(second['user_id'], second['tenant_id']))
        other = client.get('/api/creations')
        assert other.status_code == 200 and other.json()['items'] == []
        client.cookies.clear()
        assert client.get('/api/creations').status_code == 401
