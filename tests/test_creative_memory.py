"""Explicit feedback memory isolation; synthetic users and SQLite only."""
import pytest
from fastapi import HTTPException

from app.creative_memory import record_creative_memory, read_creative_memory, MAX_CONTEXT_CHARS
from app.tenancy.context import TenantContext, set_context, reset_context
from app.tenancy.resources import register_resource

pytestmark = pytest.mark.real_tenancy


@pytest.fixture
def scoped(monkeypatch, tmp_path):
    # Agent tables must join shared metadata before the first SQLite schema is
    # created, including when this test file is run independently.
    import app.gtm_agent.jobs  # noqa: F401
    from app.tenancy.database import db_connection, tenants, users
    monkeypatch.setenv('MATEQUILL_DATABASE_URL', 'sqlite:///' + (tmp_path / 'memory.sqlite3').as_posix())
    with db_connection() as conn:
        conn.execute(tenants.insert(), [{'id': 'a', 'name': 'A', 'created_at': 1},
                                       {'id': 'b', 'name': 'B', 'created_at': 1}])
        conn.execute(users.insert(), [{'id': 'u', 'email': 'u@example.test', 'name': 'U',
                                      'password_hash': 'test', 'created_at': 1, 'active': True},
                                     {'id': 'v', 'email': 'v@example.test', 'name': 'V',
                                      'password_hash': 'test', 'created_at': 1, 'active': True}])
    token = set_context(identity())
    try:
        # A selected session scope points to an actual store resource, as in
        # production. The conversational runtime reads this profile before
        # planning; permissions remain enforced by the normal resource gate.
        register_resource('store', 's1', 's1', 'store', {
            'name': '合成门店', 'location': '', 'audience': '', 'core_tasks': [], 'voice': '自然'})
        yield
    finally:
        reset_context(token)


def identity(tenant='a', store='s1', user='u', admin=True):
    return TenantContext(user_id=user, tenant_id=tenant, store_id=store,
                         roles=('admin',) if admin else ('salesperson',),
                         store_ids=(store,) if store else (), all_stores=admin)


def source(identifier='r1', store='s1', visibility='store', status='ready'):
    return register_resource('run', identifier, store, visibility,
                             {'title': '合成创作', 'status': status,
                              '_poster_task': {'platform': 'moments', 'product': '合成电脑'}})


def test_explicit_revision_persists_source_and_bounded_copy(scoped):
    source()
    memory = record_creative_memory('r1', '少一点促销，保留今天的观察。',
                                    before={'title': '旧标题', 'api_key': 'SECRET'},
                                    after={'title': '新标题', 'trace': 'PRIVATE'})
    assert memory['run_id'] == 'r1' and memory['event'] == 'revised'
    assert memory['source'] == 'explicit_user_feedback'
    assert memory['before'] == '旧标题' and memory['after'] == '新标题'
    rows = read_creative_memory('s1')
    assert rows[0]['instruction'] == memory['instruction']
    assert 'actor_id' not in rows[0] and 'before' not in rows[0]
    assert 'SECRET' not in str(memory) and 'PRIVATE' not in str(memory)


def test_empty_automatic_or_failed_draft_cannot_create_memory(scoped):
    source(status='blocked')
    with pytest.raises(ValueError): record_creative_memory('r1', '')
    with pytest.raises(ValueError): record_creative_memory('r1', '生成了', event='generated')
    with pytest.raises(HTTPException) as exc: record_creative_memory('r1', '喜欢这个风格')
    assert exc.value.status_code == 409
    assert read_creative_memory() == []


def test_cross_tenant_cannot_read_or_record_source(scoped):
    source()
    record_creative_memory('r1', '文案简短一点')
    set_context(identity(tenant='b'))
    assert read_creative_memory() == []
    with pytest.raises(HTTPException) as exc: record_creative_memory('r1', '套用风格')
    assert exc.value.status_code == 404


def test_all_store_admin_must_switch_store_and_never_get_mixed_memory(scoped):
    source()
    record_creative_memory('r1', '门店一喜欢现场观察')
    source('r2', store='s2')
    with pytest.raises(HTTPException): record_creative_memory('r2', '门店二偏好')
    with pytest.raises(HTTPException): read_creative_memory('s2')
    set_context(identity(store='s2'))
    assert read_creative_memory() == []
    record_creative_memory('r2', '门店二喜欢演示')
    assert [v['run_id'] for v in read_creative_memory()] == ['r2']
    set_context(identity())
    assert [v['run_id'] for v in read_creative_memory()] == ['r1']


def test_read_permission_enough_for_adoption_not_revision(scoped):
    source(visibility='store')
    set_context(identity(user='v', admin=False))
    event = record_creative_memory('r1', '采纳这版自然表达', event='adopted')
    assert event['event'] == 'adopted'
    with pytest.raises(HTTPException): record_creative_memory('r1', '偷偷修改别人的稿')


def test_private_source_is_reauthorized_before_memory_reaches_another_user(scoped):
    source(visibility='private')
    record_creative_memory('r1', '私有稿的偏好')
    set_context(identity(user='v', admin=False))
    assert read_creative_memory() == []


def test_recent_limit_and_total_prompt_budget(scoped):
    source()
    for index in range(25):
        record_creative_memory('r1', str(index) + '自然表达' * 300)
    rows = read_creative_memory(limit=1000)
    assert 0 < len(rows) <= 20
    assert rows[0]['instruction'].startswith('24')
    assert all(len(row['instruction']) <= 1000 for row in rows)
    assert sum(sum(len(str(v)) for v in row.values()) for row in rows) <= MAX_CONTEXT_CHARS


def test_no_selected_store_has_no_memory(scoped):
    set_context(identity(store=None))
    assert read_creative_memory() == []
    with pytest.raises(HTTPException): record_creative_memory('any', '更自然')


@pytest.mark.parametrize('audience,user,expected', [
    ('private', 'u', True), ('private', 'v', False), ('group', 'u', False), ('group', 'v', False),
])
def test_private_revision_never_enters_other_manager_or_group_context(scoped, audience, user, expected):
    import asyncio
    from app.creative_memory import creative_working_memories
    from app.gtm_agent.memory import relevant_memories
    from app.gtm_agent.tools import AgentTools
    from app.tenancy import authorize_resource
    source(visibility='private')
    feedback = '这份私有稿只保留我的窗边观察'
    record_creative_memory('r1', feedback)
    # Managers retain explicit resource-read permission, but conversational
    # retrieval deliberately applies a narrower owner/audience boundary.
    set_context(identity(user=user, admin=True))
    assert authorize_resource('run', 'r1')['body']['title'] == '合成创作'
    working = creative_working_memories(audience=audience)
    retrieved = relevant_memories(audience=audience)
    history = asyncio.run(AgentTools({'audience': audience}, {'text': '写一条'}).execute('read_history', {}))
    assert (feedback in str(working)) is expected
    assert feedback not in str(retrieved)
    assert bool(history['items']) is expected


def test_shared_source_revision_remains_history_not_group_working_memory(scoped):
    from app.gtm_agent.memory import relevant_memories
    source(visibility='store')
    record_creative_memory('r1', '本店开头不要促销口号')
    set_context(identity(user='v', admin=True))
    assert relevant_memories(audience='group') == []
    assert read_creative_memory()[0]['instruction'] == '本店开头不要促销口号'
    set_context(identity(store='s2', user='v', admin=True))
    assert relevant_memories(audience='group') == []


def test_explicit_preference_reaches_next_turn_without_replaying_edit_history(scoped):
    import asyncio
    from uuid import uuid4
    from app.creative_revision import revise_creation
    from app.models import AuditItem, EvidenceBundle, Fact, Source
    from app.tenancy import authorize_resource, transaction_context
    from app.tenancy.database import db_connection, memberships
    from app.tenancy.auth import new_session, session_row, context_from_session
    from app.gtm_agent import jobs
    from app.gtm_agent.effects import checkpoint_generated_artifact
    from app.gtm_agent.models import TurnInput
    from app.gtm_agent.repository import create_conversation, get_turn
    from app.gtm_agent.tools import AgentTools

    with db_connection() as conn:
        conn.execute(memberships.insert().values(id=uuid4().hex, user_id='u', tenant_id='a',
            role='admin', store_id=None, active=True, created_at=1))
    set_context(context_from_session(session_row(new_session('u', 'a', 's1'))))
    original = {'run_id': 'original', 'status': 'ready', 'mode': 'local', 'title': '窗边的桌面',
        'blocks': [{'text': '留一点空间给喜欢的小东西。', 'fact_ids': [], 'disclosures': []}],
        'images': [{'asset_id': 'photo', 'caption': '产品外观', 'position': '封面',
                    'role': 'cover', 'reason': '外观参考'}],
        'assets': [{'id': 'photo', 'product': '合成电脑', 'source_id': 'official',
            'url': '/api/assets/photo', 'title': '产品外观', 'verified': True,
            'provenance': 'huawei_official', 'sha256': 'a' * 64,
            'visual_role': 'product_overview', 'semantic_basis': 'pixel_review_sha256'}],
        'sources': [{'id': 'official', 'title': '官方资料', 'provider': 'huawei_official'}],
        'facts': [], '_poster_task': {'product': '合成电脑', 'audience': '本店好友',
            'scene': '桌面', 'platform': 'moments', 'mode': 'local', 'store_id': 's1'}}
    register_resource('run', 'original', store_id='s1', body=original)

    class Editor:
        async def _json(self, *args, **kwargs):
            return {'changes': [{'field': 'title', 'value': '给桌面留一点光'}]}

        async def verify(self, *args):
            return [AuditItem(severity='info', category='semantic', message='核验通过')]

    feedback = '以后开头简短，保留自然观察，不要促销口号'
    revised = asyncio.run(revise_creation('original', feedback, llm=Editor(), selection=['title']))
    assert authorize_resource('run', revised['run_id'])['body']['title'] == '给桌面留一点光'
    from app.gtm_agent.memory import remember
    preference = remember({'id': 'explicit-preference', 'text': feedback},
        kind='preference', key='opening_style', text='开头简短、自然，不用促销口号', quote=feedback)
    source('past-draft')
    historical_story = '只改第一段：我天天看顾客从福雷德广场散场，手机有999倍变焦。'
    record_creative_memory('past-draft', historical_story)
    conversation = create_conversation()
    turn = jobs.enqueue_turn(conversation['id'], TurnInput(text='再写一条新的桌面朋友圈', client_turn_id=uuid4().hex))
    decisions = [
        {'action': 'tool', 'tool': 'read_catalog'},
        {'action': 'tool', 'tool': 'read_product', 'arguments': {'product': '合成电脑'}},
        {'action': 'tool', 'tool': 'generate_content', 'arguments': {
            'product': '合成电脑', 'audience': '附近朋友', 'scene': '桌面', 'platform': 'moments'}},
    ]
    planner_calls, generated_tasks = [], []

    def planner(system, payload):
        planner_calls.append(payload)
        assert len(planner_calls) <= 3, 'A committed artifact must end this turn without a second generation'
        return decisions[len(planner_calls) - 1]

    class Provider:
        def catalog(self):
            return [{'name': '合成电脑'}]

        def retrieve(self, product, query):
            return EvidenceBundle(product=product, facts=[Fact(id='one', product=product,
                label='外观', value='合成描述', quote='合成描述', source_id='official')],
                sources=[Source(id='official', title='官方资料')])

    async def generate(task):
        generated_tasks.append(task)
        record = {'run_id': 'next-generation', 'status': 'ready', 'title': '第二次的作品',
                  '_poster_task': task.model_dump()}
        with transaction_context():
            register_resource('run', record['run_id'], body=record)
            checkpoint_generated_artifact('run', record['run_id'], record)
        return record

    asyncio.run(jobs.execute_claimed(jobs.claim_next(), planner=planner,
        tools_factory=lambda c, t: AgentTools(c, t, provider=Provider(), generator=generate)))
    assert len(generated_tasks) == 1 and len(planner_calls) == 3
    memory = planner_calls[0]['working_memory']
    assert any(item['id'] == preference['id'] and item['quote'] == feedback for item in memory)
    assert not any(item.get('source_run_id') == revised['run_id'] for item in memory)
    assert feedback in generated_tasks[0].requirements
    assert historical_story not in str(planner_calls)
    assert '999倍' not in generated_tasks[0].requirements
    assert '天天看顾客' not in generated_tasks[0].requirements
    assert any(item['instruction'] == historical_story for item in read_creative_memory())
    result = get_turn(conversation['id'], turn['id'])
    assert result['status'] == 'completed' and result['artifact']['id'] == 'next-generation'


def test_one_draft_replacement_is_historical_observation_not_persistent_style(scoped):
    from app.creative_memory import creative_working_memories
    from app.gtm_agent.memory import relevant_memories, remember
    source()
    replacement = '这次开头改成：14.2英寸的屏幕，留给今天的灵感。'
    event = record_creative_memory('r1', replacement)
    item = creative_working_memories()[0]
    assert item['kind'] == 'observation'
    assert item['scope'].startswith('historical_revision_only')
    assert item['quote'] == replacement and item['source_run_id'] == 'r1'
    assert '历史' in item['text'] or '以前' in item['text']
    assert '本次' in item['scope'] or '当前' in item['scope']
    assert '产品' in item['scope'] and ('数字' in item['text'] or '参数' in item['text'])
    # The source event remains faithful but never enters automatic recall.
    assert event['instruction'] == replacement
    lasting = remember({'id': 'explicit-later', 'text': '以后记住文案自然一点'},
        kind='correction', key='tone', text='自然表达', quote='以后记住文案自然一点')
    retrieved = relevant_memories(query='下一台新品')
    assert retrieved[0]['id'] == lasting['id'] and retrieved[0]['kind'] == 'correction'
    assert not any(row.get('source_run_id') == 'r1' for row in retrieved)
    assert replacement not in str(retrieved)
