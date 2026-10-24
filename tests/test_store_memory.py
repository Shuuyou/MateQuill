from copy import deepcopy

from fastapi import HTTPException
import pytest

from app import store_memory


@pytest.fixture
def records(monkeypatch):
    class Context:
        store_id = 'a'
        is_legacy = False
    rows = {
        'run': [{'kind': 'run', 'id': 'r', 'store_id': 'a', 'created_at': 10,
                 'body': {'title': '真实客户全是大学生', 'content': '客户收入很高',
                          'task': {'audience': '学生', 'scene': '课堂演示'},
                          'assets': [{'title': '桌面上的电脑', 'provenance': 'store_upload'},
                                     {'title': '人很多的商场', 'provenance': 'generated'}]}},
                {'kind': 'run', 'id': 'foreign', 'store_id': 'b', 'body': {'task': {'scene': '秘密'}}}],
        'strategy_feedback': [{'kind': 'strategy_feedback', 'id': 'f', 'store_id': 'a',
                               'body': {'run_id': 'r', 'event': 'edited', 'note': '语气自然一点', 'source': 'app_event'}},
                              {'kind': 'strategy_feedback', 'id': 'stale', 'store_id': 'a',
                               'body': {'run_id': 'deleted', 'event': 'adopted', 'note': '已删除的秘密'}}]}
    monkeypatch.setattr(store_memory, 'require_context', lambda: Context())
    monkeypatch.setattr(store_memory, 'list_resources', lambda kind: deepcopy(rows[kind]))
    def authorize(kind, identifier):
        if kind == 'store' and identifier == 'a':
            return {'id': identifier}
        raise HTTPException(404)
    monkeypatch.setattr(store_memory, 'authorize_resource', authorize)
    return rows


def test_memory_separates_visual_hypothesis_and_real_feedback(records):
    result = store_memory.collect_store_memory('a')
    entries = result['entries']
    assert {entry['kind'] for entry in entries} == {'content_hypothesis', 'material_observation', 'workflow_preference'}
    text = str(entries)
    assert '真实客户全是' not in text and '收入很高' not in text
    assert '商场' not in text and '秘密' not in text
    assert '语气自然一点' in text
    assert all(entry['sources'][0]['id'] in ('r', 'f') for entry in entries)


def test_memory_deduplicates_and_rederives_on_source_removal(records):
    duplicate = deepcopy(records['run'][0])
    duplicate['id'] = 'r2'
    records['run'].append(duplicate)
    entries = store_memory.collect_store_memory('a')['entries']
    observed = [entry for entry in entries if entry['kind'] == 'material_observation']
    assert len(observed) == 1 and len(observed[0]['sources']) == 2
    records['run'] = []
    assert store_memory.collect_store_memory('a')['entries'] == []


def test_memory_rejects_wrong_active_store_before_reading(records, monkeypatch):
    monkeypatch.setattr(store_memory, 'list_resources', lambda _: pytest.fail('must authorize before reads'))
    with pytest.raises(HTTPException) as exc:
        store_memory.collect_store_memory('b')
    assert exc.value.status_code == 409


def test_memory_redacts_contact_and_bounds_output(records):
    records['strategy_feedback'][0]['body']['note'] = '请联系 13812345678 mail@test.com ' + '字' * 2000
    result = store_memory.collect_store_memory('a', limit=60)
    preference = next(entry for entry in result['entries'] if entry['kind'] == 'workflow_preference')
    assert '13812345678' not in preference['text'] and 'mail@test.com' not in preference['text']
    assert len(preference['text']) <= 700
    assert len(store_memory.collect_store_memory('a', limit=1)['entries']) == 1


@pytest.mark.real_tenancy
def test_real_permissions_exclude_other_users_private_memory_and_tenant(monkeypatch, tmp_path):
    from app.tenancy import TenantContext, set_context, reset_context, register_resource
    from app.tenancy.database import db_connection, tenants, users
    monkeypatch.setenv('MATEQUILL_DATABASE_URL', 'sqlite:///' + (tmp_path / 'memory.sqlite3').as_posix())
    with db_connection() as conn:
        conn.execute(tenants.insert(), [{'id': 't', 'name': 'T', 'created_at': 1},
                                       {'id': 'other', 'name': 'Other', 'created_at': 1}])
        conn.execute(users.insert(), [{'id': user, 'email': user + '@test.local', 'name': user,
                                      'password_hash': 'x', 'active': True, 'created_at': 1}
                                     for user in ('alice', 'bob')])
    token = set_context(TenantContext(user_id='alice', tenant_id='t', store_id='s',
                                    store_ids=('s',), roles=('salesperson',)))
    try:
        register_resource('store', 's', 's', 'store', {'name': '本店'})
        register_resource('run', 'private-alice', 's', 'private', {'task': {'scene': 'Alice私有素材'}})
        set_context(TenantContext(user_id='bob', tenant_id='t', store_id='s', store_ids=('s',), roles=('salesperson',)))
        register_resource('run', 'private-bob', 's', 'private', {'task': {'scene': 'Bob私有素材'}})
        content = str(store_memory.collect_store_memory('s'))
        assert 'Bob私有素材' in content and 'Alice私有素材' not in content
        set_context(TenantContext(user_id='bob', tenant_id='other', store_id='s', store_ids=('s',), roles=('salesperson',)))
        with pytest.raises(HTTPException) as exc:
            store_memory.collect_store_memory('s')
        assert exc.value.status_code == 404
    finally:
        reset_context(token)
