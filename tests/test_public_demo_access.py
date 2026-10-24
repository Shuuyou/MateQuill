import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from app.tenant_routes import router
from app.tenancy import install_tenancy

pytestmark = pytest.mark.real_tenancy

@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv('MATEQUILL_DATABASE_URL', 'sqlite:///' + (tmp_path/'demo.sqlite').as_posix())
    monkeypatch.setenv('MATEQUILL_DEMO_ACCOUNTS', '1')
    monkeypatch.setenv('MATEQUILL_STUDIO_PUBLIC_ORIGIN', 'https://shuuyou-matequill.ms.show')
    app = FastAPI()
    install_tenancy(app)
    app.include_router(router)
    with TestClient(app) as c:
        yield c

ORIGIN = {'Origin': 'https://shuuyou-matequill.ms.show'}

def test_studio_guest_origin_is_explicit_and_cookie_can_embed(client, monkeypatch):
    assert client.post('/api/auth/guest', headers={'Origin': 'https://shuuyou-matequill.ms.show.evil.test'}).status_code == 403
    monkeypatch.setenv('MATEQUILL_COOKIE_SAMESITE', 'none')
    response = client.post('/api/auth/guest', headers=ORIGIN)
    assert response.status_code == 200, response.text
    assert response.json()['is_guest'] is True
    assert 'SameSite=none' in response.headers['set-cookie']
    assert 'Secure' in response.headers['set-cookie']

def test_demo_has_headquarters_and_three_scoped_stores(client):
    assert len(client.get('/api/auth/demo').json()['accounts']) == 4
    stores = set()
    for persona in ['fuleide', 'raffles', 'ifs']:
        response = client.post('/api/auth/demo', json={'persona': persona}, headers=ORIGIN)
        assert response.status_code == 200, response.text
        workspace = response.json()['workspace']
        assert workspace['roles'] == ['salesperson']
        assert workspace['all_stores'] is False
        stores.add(workspace['store_id'])
    assert len(stores) == 3
    response = client.post('/api/auth/demo', json={'persona': 'hq'}, headers=ORIGIN)
    assert response.json()['workspace']['all_stores'] is True
    assert response.json()['workspace']['roles'] == ['operator']
    again = client.post('/api/auth/demo', json={'persona': 'fuleide'}, headers=ORIGIN).json()
    assert again['workspace']['store_id'] in stores

def test_demo_disabled_is_not_a_public_login_backdoor(client, monkeypatch):
    monkeypatch.setenv('MATEQUILL_DEMO_ACCOUNTS', '0')
    assert client.get('/api/auth/demo').json()['accounts'] == []
    assert client.post('/api/auth/demo', json={'persona': 'hq'}, headers=ORIGIN).status_code == 404
