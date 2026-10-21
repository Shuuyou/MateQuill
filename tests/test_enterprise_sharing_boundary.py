"""Enterprise sharing is explicit read access, not a cross-store edit grant."""
import hashlib
import io
from uuid import uuid4

import pytest
from PIL import Image

from test_tenant_workflow_boundary import workspace, context, login, PASSWORD, ORIGIN
from app.tenancy import set_context, reset_context
from app.run_records import save_run

pytestmark = pytest.mark.real_tenancy


def test_enterprise_shared_run_cross_store_read_export_and_bounded_parent_photo(workspace):
    client, first, second, store_a = workspace
    store_b_response = client.post('/api/stores', json={'name': '商务店', 'audience': '职场人'}, headers=ORIGIN)
    assert store_b_response.status_code == 201
    store_b = store_b_response.json()
    output = io.BytesIO()
    Image.new('RGB', (64, 48), '#cce5da').save(output, 'JPEG')
    uploads = []
    for _ in range(2):
        response = client.post('/api/media/intake', data={'kind': 'photo'},
            files={'file': ('store.jpg', output.getvalue(), 'image/jpeg')}, headers=ORIGIN)
        assert response.status_code == 200
        uploads.append(response.json()['id'])
    identifier = uuid4().hex
    selected = 'upload-'+uploads[0]
    normalized = client.get('/api/media/'+uploads[0]).content
    token = set_context(context(client))
    try:
        save_run(identifier, {'run_id': identifier, 'status': 'ready', 'title': '企业共享的真实门店稿',
            'blocks': [{'text': '共享创意，不开放整份媒体库。'}],
            'assets': [{'id': selected, 'url': '/api/assets/'+selected, 'sha256': hashlib.sha256(normalized).hexdigest()}],
            'images': [{'asset_id': selected}],
            'creative_plan': {'cards': [{'headline': '本店实拍', 'body': '真实的工作场景', 'asset_ids': [selected]}]}})
    finally:
        reset_context(token)
    share = client.patch(f'/api/creations/run/{identifier}/sharing', json={'visibility': 'tenant'}, headers=ORIGIN)
    assert share.status_code == 200
    invitation = client.post('/api/team/invitations', json={'email': 'cross-store-sales@example.test',
        'role': 'salesperson', 'store_id': store_b['id']}, headers=ORIGIN).json()
    accepted = client.post('/api/auth/accept-invitation', json={'token': invitation['token'],
        'name': '商务店同事', 'password': PASSWORD}, headers=ORIGIN)
    assert accepted.status_code == 200
    record = client.get('/api/runs/'+identifier)
    assert record.status_code == 200
    assert identifier in [row['id'] for row in client.get('/api/creations').json()['items']]
    image = client.get(f'/api/runs/{identifier}/assets/{selected}')
    assert image.status_code == 200 and image.content == normalized
    assert client.get('/api/media/'+uploads[0]).status_code == 404
    assert client.get('/api/media/'+uploads[1]).status_code == 404
    assert client.get('/api/assets/'+selected).status_code == 404
    assert client.get(f'/api/runs/{identifier}/assets/upload-{uploads[1]}').status_code == 404
    exported = client.get(f'/api/runs/{identifier}/export?format=html')
    assert exported.status_code == 200 and 'data:image/jpeg;base64,' in exported.text
    assert client.patch(f'/api/creations/run/{identifier}/sharing', json={'visibility': 'private'}, headers=ORIGIN).status_code == 404
    # The original store remains part of the provenance; sharing is not a move.
    login(client)
    own = next(row for row in client.get('/api/creations').json()['items'] if row['id'] == identifier)
    assert own['store_id'] == store_a['id'] and own['visibility'] == 'tenant'
    assert client.patch(f'/api/creations/run/{identifier}/sharing', json={'visibility': 'private'}, headers=ORIGIN).status_code == 200
    login(client, 'cross-store-sales@example.test')
    assert client.get('/api/runs/'+identifier).status_code == 404
    assert client.get(f'/api/runs/{identifier}/assets/{selected}').status_code == 404
    assert client.get(f'/api/runs/{identifier}/export?format=html').status_code == 404
    login(client, 'hq-b@example.test')
    assert client.get('/api/runs/'+identifier).status_code == 404
