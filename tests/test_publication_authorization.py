"""Real account sessions and resource rows guard the publication snapshot."""
import asyncio
import hashlib
from uuid import uuid4

import pytest
from fastapi import HTTPException

from test_tenant_workflow_boundary import workspace, context, login, ORIGIN
from test_publication_bundle import picture, record
from app.publication_routes import load_publication
from app.run_records import save_run
from app.tenancy import set_context, reset_context

pytestmark = pytest.mark.real_tenancy


def in_workspace(client, function):
    token = set_context(context(client))
    try:
        return function()
    finally:
        reset_context(token)


def save(client, body, identifier=None):
    identifier = identifier or uuid4().hex
    body['run_id'] = identifier
    body['_poster_task'] = {'platform': 'wechat'}
    in_workspace(client, lambda: save_run(identifier, body))
    return identifier


def read(client, identifier, expected=None):
    return in_workspace(client, lambda: asyncio.run(load_publication(identifier, expected)))


def test_publication_authorization_is_real_tenant_scoped_and_version_bound(workspace):
    client, *_ = workspace
    body = record(); body['images'] = []; body['assets'] = []
    identifier = save(client, body)
    bundle = read(client, identifier)
    assert read(client, identifier, bundle.content_hash).content_hash == bundle.content_hash
    body['intro'] = '实际保存的新正文'
    save(client, body, identifier)
    with pytest.raises(HTTPException) as error: read(client, identifier, bundle.content_hash)
    assert error.value.status_code == 409
    login(client, 'hq-b@example.test')
    with pytest.raises(HTTPException) as error: read(client, identifier)
    assert error.value.status_code == 404


def test_publication_cannot_embed_other_tenants_private_photo(workspace):
    client, *_ = workspace
    upload = client.post('/api/media/intake', data={'kind': 'photo'},
                         files={'file': ('photo.png', picture(), 'image/png')}, headers=ORIGIN)
    assert upload.status_code == 200, upload.text
    identifier = upload.json()['id']
    data = client.get('/api/media/' + identifier).content
    aid = 'upload-' + identifier
    login(client, 'hq-b@example.test')
    body = record(); body['assets'] = [{'id': aid, 'sha256': hashlib.sha256(data).hexdigest()}]
    body['images'] = [{'asset_id': aid, 'caption': '私有图片', 'block_index': None}]
    run_id = save(client, body)
    with pytest.raises(HTTPException) as error: read(client, run_id)
    assert error.value.status_code == 404


def test_own_authorized_uploaded_photo_is_delivered_with_exact_file_hash(workspace):
    client, *_ = workspace
    upload = client.post('/api/media/intake', data={'kind': 'photo'},
                         files={'file': ('photo.png', picture(), 'image/png')}, headers=ORIGIN)
    assert upload.status_code == 200, upload.text
    identifier = upload.json()['id']
    data = client.get('/api/media/' + identifier).content
    aid = 'upload-' + identifier
    body = record(); body['assets'] = [{'id': aid, 'sha256': hashlib.sha256(data).hexdigest()}]
    body['images'] = [{'asset_id': aid, 'caption': '本店实拍', 'block_index': None}]
    bundle = read(client, save(client, body))
    assert bundle.pictures[0].sha256 == hashlib.sha256(data).hexdigest()
    assert bundle.pictures[0].data == data
