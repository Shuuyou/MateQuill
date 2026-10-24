import time

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import select, update

from app.tenant_routes import router
from app.generation_jobs import generation_budget, usage, usage_summary
from app.tenancy import install_tenancy, require_context, save_body, load_body, get_context
from app.tenancy.auth import COOKIE_NAME, refresh_context
from app.tenancy.database import db_connection, sessions, trials

pytestmark = pytest.mark.real_tenancy
ORIGIN = {'Origin': 'http://testserver'}
PASSWORD = 'Guest-test-only-password!'


@pytest.fixture
def app(tmp_path, monkeypatch):
    monkeypatch.setenv('MATEQUILL_DATABASE_URL', 'sqlite:///' + (tmp_path/'guests.sqlite').as_posix())
    application = FastAPI()
    install_tenancy(application)
    application.include_router(router)

    @application.get('/api/example/{identifier}')
    def read(identifier: str):
        return load_body('run', identifier)

    @application.post('/api/example/{identifier}')
    def write(identifier: str):
        context = require_context()
        return save_body('run', identifier, {'title': '我的试用作品'}, store_id=context.store_id)

    @application.post('/api/budget-test')
    def budget():
        with generation_budget():
            pass
        return usage_summary()

    @application.post('/api/backdate-usage')
    def backdate():
        context = require_context()
        with db_connection() as connection:
            connection.execute(update(usage).where(usage.c.tenant_id == context.tenant_id).values(day='2000-01-01'))
        return {'ok': True}

    @application.get('/api/live-context')
    def context():
        return {'live': refresh_context(get_context()) is not None}

    @application.get('/api/my-store')
    def store():
        return load_body('store', require_context().store_id)

    return application


def guest(client):
    response = client.post('/api/auth/guest', json={}, headers=ORIGIN)
    assert response.status_code == 200, response.text
    return response.json()


def test_guest_is_one_click_private_and_reuses_current_session(app):
    with TestClient(app) as a, TestClient(app) as b:
        assert a.post('/api/auth/guest').status_code == 403
        assert a.get('/api/example/first').status_code == 401
        first = guest(a)
        original_cookie = a.cookies.get(COOKIE_NAME)
        assert first['is_guest'] and first['workspace']['roles'] == ['salesperson']
        assert first['workspace']['store_id'] and len(first['memberships']) == 1
        again = guest(a)
        assert again == first and a.cookies.get(COOKIE_NAME) == original_cookie
        assert a.post('/api/example/first', headers=ORIGIN).status_code == 200
        second = guest(b)
        assert first['workspace']['tenant_id'] != second['workspace']['tenant_id']
        assert b.get('/api/example/first').status_code == 404
        assert a.get('/api/example/first').status_code == 200
        assert a.get('/api/team/members').status_code == 403
        assert a.post('/api/team/invitations', headers=ORIGIN, json={
            'email': 'other@example.test', 'role': 'admin'}).status_code == 403


def test_trial_claim_preserves_work_and_rotates_session(app):
    with TestClient(app) as client:
        first = guest(client)
        old_cookie = client.cookies.get(COOKIE_NAME)
        client.post('/api/example/article', headers=ORIGIN).raise_for_status()
        response = client.post('/api/auth/claim-trial', headers=ORIGIN, json={
            'email': 'keeper@example.test', 'password': PASSWORD, 'store_name': '校园体验店'})
        assert response.status_code == 200, response.text
        saved = response.json()
        assert not saved['is_guest'] and saved['workspace']['roles'] == ['admin']
        assert saved['user']['id'] == first['user']['id']
        assert saved['workspace']['tenant_id'] == first['workspace']['tenant_id']
        assert saved['workspace']['store_id'] == first['workspace']['store_id']
        assert client.cookies.get(COOKIE_NAME) != old_cookie
        assert client.get('/api/example/article').status_code == 200
        assert client.get('/api/my-store').json()['name'] == '校园体验店'
        # An already registered user cannot turn this endpoint into a role switch.
        assert client.post('/api/auth/claim-trial', headers=ORIGIN, json={
            'email': 'changed@example.test', 'password': PASSWORD, 'store_name': 'Other'}).status_code == 409
        client.post('/api/auth/logout', headers=ORIGIN).raise_for_status()
        client.cookies.clear()
        client.cookies.set(COOKIE_NAME, old_cookie)
        assert client.get('/api/example/article').status_code == 401
        client.cookies.clear()
        result = client.post('/api/auth/login', headers=ORIGIN, json={
            'email': 'keeper@example.test', 'password': PASSWORD})
        assert result.status_code == 200 and not result.json()['is_guest']
        assert client.get('/api/example/article').json()['title'] == '我的试用作品'
        assert not guest(client)['is_guest']  # never replace a signed-in account


def test_guest_claim_existing_email_rolls_back_and_never_resets_password(app):
    with TestClient(app) as a, TestClient(app) as b:
        guest(a)
        payload = {'email': 'keeper@example.test', 'password': PASSWORD, 'store_name': '第一家店'}
        a.post('/api/auth/claim-trial', headers=ORIGIN, json=payload).raise_for_status()
        second = guest(b)
        response = b.post('/api/auth/claim-trial', headers=ORIGIN, json={**payload, 'password': 'Other-test-password!'})
        assert response.status_code == 409
        assert b.get('/api/auth/me').json()['is_guest']
        payload['email'] = 'second@example.test'
        assert b.post('/api/auth/claim-trial', headers=ORIGIN, json=payload).status_code == 200
        assert b.get('/api/auth/me').json()['user']['id'] == second['user']['id']


def test_trial_budget_does_not_reset_at_midnight(app, monkeypatch):
    monkeypatch.setenv('MATEQUILL_TRIAL_CREATIONS', '1')
    with TestClient(app) as client:
        guest(client)
        result = client.post('/api/budget-test', headers=ORIGIN)
        assert result.status_code == 200 and result.json()['limit'] == 1
        client.post('/api/backdate-usage', headers=ORIGIN).raise_for_status()
        assert client.post('/api/budget-test', headers=ORIGIN).status_code == 429
        client.post('/api/auth/claim-trial', headers=ORIGIN, json={
            'email': 'keeper@example.test', 'password': PASSWORD, 'store_name': '我的门店'}).raise_for_status()
        assert client.post('/api/budget-test', headers=ORIGIN).status_code == 200


def test_expired_guest_session_cannot_read_or_continue_a_job(app):
    with TestClient(app) as client:
        state = guest(client)
        assert client.get('/api/live-context').json()['live']
        client.post('/api/example/article', headers=ORIGIN).raise_for_status()
        with db_connection() as connection:
            row = connection.execute(select(sessions).where(sessions.c.user_id == state['user']['id'])).mappings().one()
            assert abs(row['expires_at'] - state['trial_expires_at']) < 5
            connection.execute(update(trials).where(trials.c.user_id == state['user']['id']).values(expires_at=time.time()-1))
        assert client.get('/api/example/article').status_code == 401
        assert client.get('/api/live-context').status_code == 401


def test_trial_capacity_failure_leaves_no_partial_workspace(app, monkeypatch):
    monkeypatch.setenv('MATEQUILL_TRIAL_STARTS_PER_DAY', '1')
    with TestClient(app) as a, TestClient(app) as b:
        guest(a)
        assert b.post('/api/auth/guest', headers=ORIGIN).status_code == 429
        assert guest(a)['is_guest']  # existing visitors keep working at capacity
        with db_connection() as connection:
            assert len(connection.execute(select(trials)).all()) == 1
