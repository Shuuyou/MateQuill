"""Editorial diversity honors evidence, permissions and real provider state."""
import asyncio
from copy import deepcopy
from contextlib import contextmanager
import time

import pytest
from sqlalchemy import event

from app.regional_diversity import authorized_history, describe, select_candidates
from app.tenancy import TenantContext, set_context, reset_context, save_body, get_engine
from app.tenancy.database import db_connection, tenants, users, clear_engine_cache


def candidate(identifier='night', **changes):
    return {'id': identifier, 'customer_task': '夜景拍摄，拍清灯箱文字',
        'local_observation': '图书馆闭馆后的灯箱小字', 'visual_structure': '局部放大招牌文字',
        'angle': '散场之后，认真看清一处招牌', 'mechanism': 'detail_reveal', **changes}


def choose(rows, history=(), **kwargs):
    return asyncio.run(select_candidates(rows, history=history, **kwargs))


def test_shared_product_values_and_conditions_are_not_copied_creative_content():
    facts = [{'id': 'f:1', 'value': '128GB', 'quote': '128GB，实际可用空间因配置而异',
        'qualifiers': ['实际可用空间因配置而异']}]
    old = candidate('old', customer_task='128GB，实际可用空间因配置而异', local_observation='', visual_structure='', angle='')
    new = candidate('new', customer_task='128GB，实际可用空间因配置而异', local_observation='', visual_structure='', angle='')
    original = deepcopy((old, new, facts))
    result = choose([new], [old], common_facts=facts)
    assert result['rankings'][0]['redundancy'] == 0
    assert (old, new, facts) == original
    assert describe(new, common_facts=facts)['customer_task'].strip('，, ') == ''


def test_rephrased_same_task_and_observation_lose_to_a_new_customer_task():
    history = [{**candidate('old'), 'source_id': 'channel_submission:old'}]
    same = candidate('same', customer_task='暗光下记录招牌', visual_structure='特写招牌文字')
    fresh = candidate('study', customer_task='整理课堂笔记与课程资料',
        local_observation='课间收齐同组同学的讲义', visual_structure='对照桌面与归档后的资料', angle='把课间的零散资料收在一起')
    result = choose([same, fresh], history, preferred_id='same')
    same_rank = next(row for row in result['rankings'] if row['candidate_id'] == 'same')
    assert same_rank['matches'][0]['same_task'] is True
    assert result['selected_id'] == 'study'
    assert result['method'] == 'facet_mmr' and result['embedding_state'] == 'not_configured'


def test_same_capability_with_a_different_local_observation_adds_coverage():
    history = [candidate('old')]
    new = candidate('new', local_observation='便利店橱窗映出的霓虹反光',
        angle='买完夜宵，看看玻璃里多出来的颜色')
    result = choose([candidate('repeat'), new], history, preferred_id='repeat')
    assert result['selected_id'] == 'new'
    scores = {row['candidate_id']: row for row in result['rankings']}
    assert scores['new']['coverage_gain'] > scores['repeat']['coverage_gain']


def test_deterministic_selection_and_invalid_candidates_never_look_complete():
    rows = [candidate('b'), candidate('a')]
    assert choose(rows)['selected_id'] == choose(list(reversed(rows)))['selected_id'] == 'a'
    assert choose([])['status'] == 'unavailable'
    assert choose([candidate('same'), candidate('same')])['selected_id'] is None


def test_embedding_is_a_real_injected_batch_and_failure_is_explicit():
    calls = []
    async def embedding(texts):
        calls.append(texts)
        return [[1, 0], [0, 1], [1, 0]]
    result = choose([candidate('same'), candidate('other', customer_task='课程资料整理')],
        [{**candidate('old'), 'source_id': 'shared:1'}], embedder=embedding)
    assert len(calls) == 1 and len(calls[0]) == 3
    assert result['embedding_state'] == 'computed'
    assert result['method'] == 'embedding_facet_mmr'
    assert result['source_ids'] == ['shared:1']
    async def broken(texts):
        raise RuntimeError('provider token must never leak')
    failed = choose([candidate()], [candidate('old')], embedder=broken)
    assert failed['embedding_state'] == 'failed'
    assert failed['method'] == 'facet_mmr'
    assert 'provider token' not in str(failed)
    invalid = choose([candidate()], [candidate('old')], embedder=lambda texts: [[float('nan')], [1]])
    assert invalid['embedding_state'] == 'failed'


@pytest.mark.real_tenancy
def test_history_never_reads_private_bodies_or_unauthorized_stores(tmp_path, monkeypatch):
    monkeypatch.setenv('MATEQUILL_DATABASE_URL', 'sqlite:///' + (tmp_path/'history.sqlite').as_posix())
    clear_engine_cache()
    with db_connection() as connection:
        for tenant in ('a', 'b'):
            connection.execute(tenants.insert().values(id=tenant, name=tenant, created_at=time.time()))
        connection.execute(users.insert().values(id='hq', email='hq@demo.test', name='HQ',
            password_hash='not-a-login', active=True, created_at=time.time()))
    @contextmanager
    def acting(tenant='a', store=None, admin=True):
        token = set_context(TenantContext('hq', tenant, store_id=store,
            roles=('admin' if admin else 'salesperson',), store_ids=(store,) if store else (),
            all_stores=admin, grants=(('admin', None),) if admin else (('salesperson', store),)))
        try:
            yield
        finally:
            reset_context(token)
    body = {'status': 'prepared', 'campaign_id': 'campaign', 'plan': candidate()}
    with acting():
        save_body('channel_opportunity', 'shared-one', body, store_id='one', visibility='store')
        save_body('channel_opportunity', 'shared-two', body, store_id='two', visibility='store')
        save_body('channel_opportunity', 'secret-draft', {**body, 'secret': 'PRIVATE_CUSTOMER'}, store_id='one')
        save_body('channel_submission', 'not-approved', {'status': 'submitted', 'content_snapshot': {'intro': 'unreviewed'}}, store_id='one', visibility='store')
        save_body('channel_submission', 'approved', {'status': 'approved', 'content_snapshot': {
            'platform': 'moments', 'intro': '下课后的讲义', '_poster_task': {'scene': '整理课程资料'}}}, store_id='one', visibility='store')
    with acting('b'):
        save_body('channel_opportunity', 'foreign', body, store_id='one', visibility='store')
    selected = []
    def track(conn, cursor, statement, parameters, context, many):
        if statement.lstrip().upper().startswith('SELECT') and 'body' in statement:
            selected.append(str(parameters))
    engine = get_engine()
    event.listen(engine, 'before_cursor_execute', track)
    try:
        with acting(store='one', admin=False):
            records = authorized_history({'platform': 'moments', 'store_id': 'one'})
            assert {row['source_id'] for row in records} == {'channel_opportunity:shared-one', 'channel_submission:approved'}
            regional = authorized_history(campaign_id='campaign')
            assert {row['source_id'] for row in regional} == {'channel_opportunity:shared-one'}
        with acting():
            assert len(authorized_history(campaign_id='campaign')) == 2
        assert not any('secret-draft' in query or 'foreign' in query for query in selected)
        assert 'PRIVATE_CUSTOMER' not in str(records)
    finally:
        event.remove(engine, 'before_cursor_execute', track)
        clear_engine_cache()
