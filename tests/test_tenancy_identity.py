import time
from dataclasses import replace

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient
from sqlalchemy import select, update

from app.tenant_routes import router
from app.tenancy import (TenantContext, authorize_resource, get_context, install_tenancy,
    list_resources, load_body, register_resource, reset_context, save_body, set_context, transaction_context)
from app.tenancy.auth import refresh_context
from app.tenancy.database import db_connection, memberships
from scripts.tenant_admin import bootstrap

pytestmark = pytest.mark.real_tenancy
PASSWORD = 'A-test-only-password-2026!'
ORIGIN = {'Origin': 'http://testserver'}


@pytest.fixture
def setup_identity(tmp_path, monkeypatch):
    monkeypatch.setenv('MATEQUILL_DATABASE_URL', 'sqlite:///' + (tmp_path / 'tenancy.sqlite3').as_posix())
    first = bootstrap(email='first@example.test', name='总部甲', tenant_name='甲企业', password=PASSWORD)
    second = bootstrap(email='second@example.test', name='总部乙', tenant_name='乙企业', password=PASSWORD)
    app = FastAPI()
    app.include_router(router)
    install_tenancy(app)

    @app.get('/api/private')
    def protected():
        context = get_context()
        return {'tenant_id': context.tenant_id, 'user_id': context.user_id}

    @app.get('/api/resource/{kind}/{identifier}')
    def resource(kind: str, identifier: str):
        return load_body(kind, identifier)

    @app.post('/api/resource/{kind}/{identifier}')
    def create(kind: str, identifier: str):
        return save_body(kind, identifier, {'value': 'private'})

    with TestClient(app) as client:
        yield client, first, second


def login(client, email='first@example.test'):
    response = client.post('/api/auth/login', json={'email': email, 'password': PASSWORD}, headers=ORIGIN)
    assert response.status_code == 200, response.text
    return response.json()


def create_store(first, store_id='store-a'):
    token = set_context(TenantContext(first['user_id'], first['tenant_id'], roles=('admin',),
                                     all_stores=True, grants=(('admin', None),)))
    try:
        register_resource('store', store_id, store_id=store_id, visibility='store', body={'id': store_id, 'name': '示例店'})
    finally:
        reset_context(token)


def test_default_deny_and_me(setup_identity):
    client, *_ = setup_identity
    assert client.get('/api/private').status_code == 401
    assert client.get('/api/auth/me').json()['authenticated'] is False


def test_password_and_cookie_security(setup_identity):
    client, first, _ = setup_identity
    failed = client.post('/api/auth/login', json={'email': 'first@example.test', 'password': 'wrong'}, headers=ORIGIN)
    assert failed.status_code == 401
    response = client.post('/api/auth/login', json={'email': 'first@example.test', 'password': PASSWORD}, headers=ORIGIN)
    cookie = response.headers['set-cookie'].lower()
    assert 'httponly' in cookie and 'samesite=lax' in cookie
    assert 'password' not in response.text and 'token' not in response.text
    assert client.get('/api/private').json()['tenant_id'] == first['tenant_id']


def test_origin_required_for_cookie_mutations(setup_identity):
    client, *_ = setup_identity
    login(client)
    assert client.post('/api/auth/logout').status_code == 403
    assert client.post('/api/auth/logout', headers={'Origin': 'https://evil.example'}).status_code == 403
    assert client.get('/api/private').status_code == 200
    assert client.post('/api/auth/logout', headers=ORIGIN).status_code == 200
    assert client.get('/api/private').status_code == 401


def test_tenant_id_headers_do_not_choose_workspace(setup_identity):
    client, first, second = setup_identity
    login(client)
    result = client.get('/api/private', headers={'X-Tenant-ID': second['tenant_id']})
    assert result.json()['tenant_id'] == first['tenant_id']
    switch = client.post('/api/auth/workspace', json={'tenant_id': second['tenant_id']}, headers=ORIGIN)
    assert switch.status_code == 404


def test_resource_id_is_not_an_access_token(setup_identity):
    client, *_ = setup_identity
    login(client)
    assert client.post('/api/resource/run/known-id', headers=ORIGIN).status_code == 200
    assert client.get('/api/resource/run/known-id').status_code == 200
    login(client, 'second@example.test')
    assert client.get('/api/resource/run/known-id').status_code == 404
    assert client.post('/api/resource/run/known-id', headers=ORIGIN).status_code == 404
    login(client)
    assert client.get('/api/resource/run/known-id').json()['value'] == 'private'


def test_invitation_single_use_and_scoped_workspace(setup_identity):
    client, first, _ = setup_identity
    create_store(first)
    login(client)
    response = client.post('/api/team/invitations', json={'email': 'sales@example.test',
        'role': 'salesperson', 'store_id': 'store-a'}, headers=ORIGIN)
    assert response.status_code == 201
    invite = response.json()
    body = {'token': invite['token'], 'name': '销售员', 'password': PASSWORD}
    accepted = client.post('/api/auth/accept-invitation', json=body, headers=ORIGIN)
    assert accepted.status_code == 200
    assert accepted.json()['workspace']['store_id'] == 'store-a'
    assert accepted.json()['workspace']['roles'] == ['salesperson']
    assert client.post('/api/team/invitations', json={'email': 'bad@example.test', 'role': 'admin'}, headers=ORIGIN).status_code == 403
    assert client.post('/api/auth/accept-invitation', json=body, headers=ORIGIN).status_code == 410
    assert client.post('/api/auth/workspace', json={'tenant_id': first['tenant_id'], 'store_id': 'other-store'}, headers=ORIGIN).status_code == 404


def test_revocation_changes_next_request_and_job_context(setup_identity):
    client, first, _ = setup_identity
    create_store(first)
    login(client)
    invite = client.post('/api/team/invitations', json={'email': 'sales@example.test', 'role': 'salesperson',
        'store_id': 'store-a'}, headers=ORIGIN).json()
    client.post('/api/auth/accept-invitation', json={'token': invite['token'], 'name': '销售员', 'password': PASSWORD}, headers=ORIGIN)
    from app.tenancy.auth import session_row, context_from_session
    context = context_from_session(session_row(client.cookies.get('matequill_session')))
    assert refresh_context(context) is not None
    with db_connection() as connection:
        connection.execute(update(memberships).where(memberships.c.user_id == context.user_id).values(active=False))
    assert client.get('/api/private').status_code == 401
    assert refresh_context(context) is None


def test_same_store_private_vs_shared_and_manager_scope(setup_identity):
    _, first, _ = setup_identity
    owner = TenantContext('owner', first['tenant_id'], 'store-a', ('salesperson',), ('store-a',),
                          grants=(('salesperson', 'store-a'),))
    token = set_context(owner)
    try:
        register_resource('media', 'personal', body={'caption': 'private'})
        register_resource('media', 'shared', visibility='store', body={'caption': 'shared'})
        set_context(replace(owner, user_id='colleague'))
        assert len(list_resources('media')) == 1
        with pytest.raises(HTTPException) as error:
            authorize_resource('media', 'personal')
        assert error.value.status_code == 404
        with pytest.raises(HTTPException):
            authorize_resource('media', 'shared', 'edit')
        set_context(replace(owner, user_id='manager', roles=('store_manager',), grants=(('store_manager', 'store-a'),)))
        assert authorize_resource('media', 'personal')['owner_id'] == 'owner'
        set_context(replace(owner, user_id='manager', store_id='store-b', store_ids=('store-b',),
                            roles=('store_manager',), grants=(('store_manager', 'store-b'),)))
        with pytest.raises(HTTPException):
            authorize_resource('media', 'personal')
    finally:
        reset_context(token)


def test_existing_invited_account_cannot_reset_password(setup_identity):
    client, first, second = setup_identity
    login(client)
    invite = client.post('/api/team/invitations', json={'email': 'second@example.test', 'role': 'operator'}, headers=ORIGIN).json()
    accepted = client.post('/api/auth/accept-invitation', json={'token': invite['token'], 'name': 'different',
        'password': 'New-malicious-password-2026!'}, headers=ORIGIN)
    assert accepted.status_code == 401
    login(client, 'second@example.test')
    assert client.get('/api/auth/me').json()['workspace']['tenant_id'] == second['tenant_id']


def test_last_admin_cannot_be_revoked(setup_identity):
    client, *_ = setup_identity
    login(client)
    member = client.get('/api/team/members').json()['members'][0]
    assert client.delete('/api/team/members/' + member['id'], headers=ORIGIN).status_code == 409


def test_tenant_wide_campaign_not_bound_to_selected_store(setup_identity):
    _, first, _ = setup_identity
    context = TenantContext(first['user_id'], first['tenant_id'], 'store-a', ('admin',), all_stores=True,
                            grants=(('admin', None),))
    token = set_context(context)
    try:
        resource = register_resource('campaign', 'global-campaign', visibility='tenant', body={'title': 'activity'})
        assert resource['store_id'] is None
    finally:
        reset_context(token)


def test_store_manager_cannot_read_headquarters_private_content(setup_identity):
    _, first, _ = setup_identity
    token = set_context(TenantContext(first['user_id'], first['tenant_id'], roles=('admin',),
                                     all_stores=True, grants=(('admin', None),)))
    try:
        register_resource('run', 'hq-private', body={'private': True})
        set_context(TenantContext('store-manager', first['tenant_id'], 'store-a', ('store_manager',),
            ('store-a',), grants=(('store_manager', 'store-a'),)))
        assert get_context().scoped_roles(None) == ()
        assert get_context().scoped_roles() == ('store_manager',)
        with pytest.raises(HTTPException):
            authorize_resource('run', 'hq-private')
    finally:
        reset_context(token)


def test_composed_workflow_rolls_back_all_resources(setup_identity):
    _, first, _ = setup_identity
    token = set_context(TenantContext(first['user_id'], first['tenant_id'], roles=('admin',),
                                     all_stores=True, grants=(('admin', None),)))
    try:
        with pytest.raises(RuntimeError):
            with transaction_context():
                save_body('assignment', 'atomic-test', {'status': 'draft'})
                save_body('submission', 'atomic-submit', {'status': 'draft'})
                raise RuntimeError('cancel this transition')
        assert list_resources('assignment') == []
        assert list_resources('submission') == []
    finally:
        reset_context(token)


def test_company_roles_cannot_be_assigned_to_single_store(setup_identity):
    client, first, _ = setup_identity
    create_store(first)
    login(client)
    assert client.post('/api/team/invitations', json={'email': 'bad-role@example.test', 'role': 'admin',
        'store_id': 'store-a'}, headers=ORIGIN).status_code == 422


def test_stale_tab_workspace_precondition_does_not_change_identity(setup_identity):
    client, first, second = setup_identity
    login(client)
    assert client.get('/api/private', headers={'X-MateQuill-Workspace': first['tenant_id'] + ':'}).status_code == 200
    stale = client.get('/api/private', headers={'X-MateQuill-Workspace': second['tenant_id'] + ':'})
    assert stale.status_code == 409 and stale.json()['code'] == 'workspace_changed'
    mutation = client.post('/api/resource/run/stale-tab', headers={**ORIGIN,
        'X-MateQuill-Workspace': first['tenant_id'] + ':wrong-store'})
    assert mutation.status_code == 409
    # The precondition cannot change authentication and cannot block refreshing me.
    assert client.get('/api/auth/me', headers={'X-MateQuill-Workspace': 'obsolete:'}).json()['workspace']['tenant_id'] == first['tenant_id']


def test_background_identity_keeps_authorized_original_tenant_after_switch(setup_identity):
    client, first, second = setup_identity
    login(client, 'second@example.test')
    invited = client.post('/api/team/invitations', json={'email': 'first@example.test', 'role': 'operator'}, headers=ORIGIN).json()
    client.post('/api/auth/accept-invitation', json={'token': invited['token'], 'name': '总部甲', 'password': PASSWORD}, headers=ORIGIN)
    login(client)
    from app.tenancy.auth import session_row, context_from_session
    original = context_from_session(session_row(client.cookies.get('matequill_session')))
    assert original.tenant_id == first['tenant_id']
    switched = client.post('/api/auth/workspace', json={'tenant_id': second['tenant_id']}, headers=ORIGIN)
    assert switched.status_code == 200
    refreshed = refresh_context(original)
    assert refreshed and refreshed.tenant_id == first['tenant_id']
    assert refreshed.roles == ('admin',)
    client.post('/api/auth/logout', headers=ORIGIN)
    assert refresh_context(original) is None
