"""Real tenant resources plus mocked model transport; no external services."""
import asyncio
from copy import deepcopy
import hashlib
from uuid import uuid4

from fastapi import HTTPException
import pytest

from app.creative_revision import revise_creation
from app.models import AuditItem
from app.tenancy import (authorize_resource, list_resources, register_resource,
                         reset_context, save_body, set_context)

pytestmark = pytest.mark.real_tenancy


def run(coro):
    return asyncio.run(coro)


@pytest.fixture
def workspace(tmp_path, monkeypatch):
    from app.tenancy.database import db_connection, tenants, users, memberships
    from app.tenancy.auth import context_from_session, new_session, session_row
    monkeypatch.setenv('MATEQUILL_DATABASE_URL', 'sqlite:///' + (tmp_path / 'revision.sqlite3').as_posix())
    with db_connection() as conn:
        conn.execute(tenants.insert(), [{'id': 'ta', 'name': 'A', 'created_at': 1},
                                       {'id': 'tb', 'name': 'B', 'created_at': 1}])
        conn.execute(users.insert(), [{'id': name, 'email': name + '@example.test',
            'name': name, 'password_hash': 'synthetic', 'active': True, 'created_at': 1}
            for name in ('u', 'v')])
        conn.execute(memberships.insert(), [{'id': uuid4().hex, 'user_id': 'u',
            'tenant_id': tenant, 'role': 'admin', 'store_id': None, 'active': True, 'created_at': 1}
            for tenant in ('ta', 'tb')])
        conn.execute(memberships.insert().values(id=uuid4().hex, user_id='v', tenant_id='ta',
            role='salesperson', store_id='s1', active=True, created_at=1))

    def switch(tenant='ta', store='s1', user='u'):
        identity = context_from_session(session_row(new_session(user, tenant, store)))
        set_context(identity)
        return identity

    token = set_context(None)
    switch()
    parent = {
        'run_id': 'parent', 'status': 'ready', 'mode': 'local', 'title': '桌面的一点光',
        'intro': '', 'cta': '你更喜欢什么样的桌面？', 'tags': [],
        'blocks': [{'text': '重量约 1.3 kg。', 'fact_ids': ['f1'],
                    'disclosures': ['不同配置可能有差异']},
                   {'text': '留一点空间给喜欢的小东西。', 'fact_ids': []}],
        '_poster_task': {'product': '合成电脑', 'audience': '本店好友', 'scene': '桌面',
                         'platform': 'moments', 'mode': 'local', 'store_id': 's1'},
        'facts': [{'id': 'f1', 'product': '合成电脑', 'label': '重量', 'value': '1.3 kg',
            'quote': '重量约 1.3 kg，不同配置可能有差异。', 'source_id': 's1',
            'qualifiers': ['不同配置可能有差异']}],
        # The app retains original hash but sanitizes public excerpts. The
        # derived audit view must not compare a cropped excerpt to that hash.
        'sources': [{'id': 's1', 'title': '合成官方资料', 'provider': 'huawei_official',
            'url': 'https://consumer.huawei.com/cn/', 'excerpt': '公开摘要',
            'sha256': hashlib.sha256(b'original-document').hexdigest()}],
        'assets': [{'id': 'a1', 'product': '合成电脑', 'source_id': 's1',
            'url': '/api/assets/a1', 'title': '官方产品外观', 'provenance': 'huawei_official',
            'verified': True, 'sha256': 'a' * 64, 'visual_role': 'product_overview',
            'semantic_basis': 'pixel_review_sha256'}],
        'images': [{'asset_id': 'a1', 'caption': '官方产品外观', 'position': '封面',
                    'role': 'cover', 'reason': '产品外观参考'}],
        'creative_plan': {}, 'strategy': {}, 'audit': [],
    }
    register_resource('run', 'parent', store_id='s1', visibility='store', body=parent)
    try:
        yield parent, switch
    finally:
        reset_context(token)


class Model:
    def __init__(self, changes=None, *, supported=True, mutate=None):
        self.patch = changes if changes is not None else {'changes': [
            {'field': 'title', 'value': '给桌面留一点光'}]}
        self.supported = supported
        self.mutate = mutate
        self.calls = []

    async def _json(self, system, payload, **kwargs):
        self.calls.append(('write', deepcopy(payload)))
        if self.mutate:
            self.mutate()
        return self.patch

    async def verify(self, result, bundle):
        self.calls.append(('verify', result.model_dump()))
        return [AuditItem(severity='info' if self.supported else 'error', category='semantic',
                          message='事实直接支持，核验通过' if self.supported else '出现未支持主张')]


def test_title_revision_creates_child_keeps_parent_images_evidence_and_memory(workspace):
    parent, _ = workspace
    model = Model()
    result = run(revise_creation('parent', '只改开头，照片保持', llm=model, selection=['title']))
    assert result['title'] == '给桌面留一点光'
    for key in ('blocks', 'images', 'assets', 'facts', 'sources', '_poster_task'):
        assert result[key] == parent[key]
    assert authorize_resource('run', 'parent')['body'] == parent
    saved = authorize_resource('run', result['run_id'])
    assert saved['store_id'] == 's1' and saved['body'] == result
    assert result['revision']['parent_run_id'] == 'parent'
    assert result['revision']['root_run_id'] == 'parent' and result['revision']['number'] == 1
    memories = list_resources('creative_memory')
    assert len(memories) == 1 and memories[0]['id'] == result['revision']['memory_id']
    assert [entry[0] for entry in model.calls] == ['write', 'verify']


def test_saved_daily_text_delivery_survives_revision_without_old_reader_proof(workspace):
    parent, _ = workspace
    parent['images'] = []
    parent['strategy'] = {'content_delivery': {
        'requested': 'text_with_optional_existing_images', 'delivered': 'text_only',
        'new_images_generated': False}, 'reader_value': {'draft_hash': 'old-draft'}}
    save_body('run', 'parent', parent, store_id='s1', visibility='store')
    result = run(revise_creation('parent', '只改标题', llm=Model(), selection=['title']))
    assert result['status'] == 'ready' and result['images'] == []
    assert result['strategy']['content_delivery']['delivered'] == 'text_only'
    assert 'reader_value' not in result['strategy']
    assert authorize_resource('run', 'parent')['body']['strategy']['reader_value']['draft_hash'] == 'old-draft'


def test_body_revision_preserves_citations_qualifiers_other_block_and_bound_card(workspace):
    parent, _ = workspace
    parent['creative_plan'] = {'cards': [{'id': 'one', 'body': parent['blocks'][0]['text'],
        'block_index': 0, 'fact_ids': ['f1'], 'disclosures': ['不同配置可能有差异'],
        'visual_intent': '展示一项重量事实', 'truth_status': 'diagram'}],
        'reader_gain': '了解桌面设备', 'reader_tension': '日常携带', 'test_variants': []}
    from app.creative_connectors import get_connector
    parent['creative_plan'] = get_connector('moments').normalize_plan(parent['creative_plan'])
    save_body('run', 'parent', parent)
    model = Model({'changes': [{'field': 'blocks.0.text', 'value': '约 1.3 kg 的重量。'}]})
    result = run(revise_creation('parent', '把第一段改自然一点', llm=model, selection=['blocks.0.text']))
    assert result['blocks'][0]['fact_ids'] == ['f1']
    assert result['blocks'][0]['disclosures'] == ['不同配置可能有差异']
    assert result['blocks'][1] == parent['blocks'][1]
    assert result['creative_plan']['cards'][0]['body'] == result['blocks'][0]['text']
    assert result['images'] == parent['images']


def test_version_chain_keeps_root(workspace):
    first = run(revise_creation('parent', '改短开头', llm=Model()))
    second = run(revise_creation(first['run_id'], '再改结尾', llm=Model({'changes': [
        {'field': 'cta', 'value': '喜欢这个光线。'}]})))
    assert second['revision']['parent_run_id'] == first['run_id']
    assert second['revision']['root_run_id'] == 'parent' and second['revision']['number'] == 2


@pytest.mark.parametrize('selection', [['images'], ['blocks.9.text'], [], ['title', 'title']])
def test_invalid_selections_fail_before_model(workspace, selection):
    model = Model()
    with pytest.raises(HTTPException) as error:
        run(revise_creation('parent', '改一下', llm=model, selection=selection))
    assert error.value.status_code == 422 and model.calls == []


def test_model_cannot_change_nonselected_field_or_replace_evidence(workspace):
    for patch, expected in [({'changes': [{'field': 'cta', 'value': '变了'}]}, 422),
                            ({'changes': [{'field': 'images', 'value': 'changed'}]}, 422),
                            ({'changes': [{'field': 'title', 'value': '变了'}], 'facts': []}, 502)]:
        with pytest.raises(HTTPException) as error:
            run(revise_creation('parent', '只改标题', llm=Model(patch), selection=['title']))
        assert error.value.status_code == expected
    assert len(list_resources('run')) == 1


def test_new_numeric_fact_is_rejected_even_if_mock_semantic_would_accept(workspace):
    model = Model({'changes': [{'field': 'blocks.0.text', 'value': '重量约 0.5 kg。'}]})
    with pytest.raises(HTTPException) as error:
        run(revise_creation('parent', '写得更轻', llm=model))
    assert error.value.status_code == 422
    assert [entry[0] for entry in model.calls] == ['write']
    assert len(list_resources('run')) == 1 and not list_resources('creative_memory')


def test_semantic_rejection_does_not_create_a_version_or_memory(workspace):
    with pytest.raises(HTTPException) as error:
        run(revise_creation('parent', '改自然一点', llm=Model(supported=False)))
    assert error.value.status_code == 422
    assert len(list_resources('run')) == 1 and not list_resources('creative_memory')


@pytest.mark.parametrize('tenant,store,user', [('tb', 's1', 'u'), ('ta', 's2', 'u'), ('ta', 's1', 'v')])
def test_cross_tenant_store_and_readonly_peer_rejected_before_prompt(workspace, tenant, store, user):
    _, switch = workspace
    switch(tenant, store, user)
    model = Model()
    with pytest.raises(HTTPException) as error:
        run(revise_creation('parent', '改标题', llm=model))
    assert error.value.status_code == 404 and model.calls == []


def test_parent_update_during_generation_prevents_saving(workspace):
    parent, _ = workspace
    def mutate():
        save_body('run', 'parent', {**parent, 'title': '别处修改的新标题'})
    with pytest.raises(HTTPException) as error:
        run(revise_creation('parent', '改标题', llm=Model(mutate=mutate)))
    assert error.value.status_code == 409
    assert len(list_resources('run')) == 1


def test_memory_failure_rolls_back_child_not_silent_success(workspace, monkeypatch):
    def fail(*args, **kwargs):
        raise RuntimeError('memory unavailable')
    monkeypatch.setattr('app.creative_memory.record_creative_memory', fail)
    with pytest.raises(RuntimeError, match='memory unavailable'):
        run(revise_creation('parent', '改标题', llm=Model()))
    assert len(list_resources('run')) == 1


def test_expired_session_is_rechecked_after_generation(workspace):
    from app.tenancy import get_context
    from app.tenancy.database import db_connection, sessions
    context = get_context()
    def expire():
        with db_connection() as conn:
            conn.execute(sessions.update().where(sessions.c.token_hash == context.session_hash).values(expires_at=0))
    with pytest.raises(HTTPException) as error:
        run(revise_creation('parent', '改标题', llm=Model(mutate=expire)))
    assert error.value.status_code == 403
    assert len(list_resources('run')) == 1
