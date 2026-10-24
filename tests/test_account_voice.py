"""Account voice uses explicit style feedback with live tenant/source isolation."""
import time

import pytest

from app.account_voice import build_account_voice
from app.models import TaskInput
from app.tenancy.context import get_context, set_context, reset_context
from app.tenancy.resources import register_resource
from app.creative_memory import record_creative_memory
from test_creative_memory import scoped, identity, source


def task(platform='xiaohongshu', **kwargs):
    return TaskInput(platform=platform, **kwargs)


def test_platforms_have_distinct_expression_and_portable_emoji_without_fixed_quotas():
    token = set_context(None)
    try:
        sheets = {platform: build_account_voice(task(platform)) for platform in ('xiaohongshu', 'wechat', 'moments', 'poster')}
        assert all(sheet['memory_state'] == 'defaults' and sheet['source_ids'] == [] for sheet in sheets.values())
        assert len({str(sheet['style_rules']) for sheet in sheets.values()}) == 4
        assert sheets['xiaohongshu']['emoji_policy']['examples']
        assert sheets['wechat']['emoji_policy']['mode'] == 'optional_unicode'
        assert sheets['poster']['emoji_policy']['mode'] == 'visual_concept_dependent'
        assert '数量配额' in sheets['xiaohongshu']['emoji_policy']['selection']
    finally:
        reset_context(token)


def test_publisher_voice_uses_resolved_strategy_without_requiring_history():
    token = set_context(None)
    try:
        brand = build_account_voice(task(), {'strategy_context': {'publisher_identity': 'brand'}})
        staff = build_account_voice(task(), {'strategy_context': {'publisher_identity': 'salesperson'}})
        regional = build_account_voice(task(), {'strategy_context': {'publisher_identity': 'provincial_agent'}})
        assert '新品' in brand['style_rules'][-1]
        assert '店员本人' in staff['style_rules'][-1]
        assert regional['publisher_identity'] == 'regional_agent'
    finally:
        reset_context(token)


@pytest.mark.real_tenancy

def test_actual_revision_and_adoption_are_traceable_but_copy_is_not_reused(scoped):
    register_resource('store', 's1', 's1', 'store', {'name': '本店'})
    source()
    record_creative_memory('r1', '语气自然一点，不要emoji。', before={'title': '旧标题 999GB'}, after={'title': '新标题 999GB'})
    sheet = build_account_voice(task('moments'))
    assert sheet['memory_state'] == 'observed_preferences'
    assert sheet['emoji_policy']['mode'] == 'none'
    assert sheet['learned_preferences'][0]['text'] == '语气自然一点，不要emoji。'
    assert sheet['source_ids'][0].startswith('creative-memory:')
    assert '999GB' not in str(sheet)
    assert '新标题' not in str(sheet)


@pytest.mark.real_tenancy

def test_adoption_without_style_statement_does_not_invent_preference(scoped):
    register_resource('store', 's1', 's1', 'store', {'name': '本店'})
    source()
    record_creative_memory('r1', '采用这版', event='adopted')
    sheet = build_account_voice(task('moments'))
    assert sheet['memory_state'] == 'observed_usage'
    assert sheet['learned_preferences'] == []
    assert sheet['selection_signals'][0]['event'] == 'adopted'
    assert '成交' not in str(sheet['selection_signals'])


@pytest.mark.real_tenancy

def test_other_store_tenant_and_other_managers_private_source_never_shape_voice(scoped):
    register_resource('store', 's1', 's1', 'store', {'name': '本店'})
    source(visibility='private')
    record_creative_memory('r1', '语气要活泼，这是私有稿')
    assert build_account_voice(task('moments'))['learned_preferences']
    set_context(identity(user='v', admin=True))
    assert build_account_voice(task('moments'))['learned_preferences'] == []
    set_context(identity(store='s2'))
    register_resource('store', 's2', 's2', 'store', {'name': '另一店'})
    assert build_account_voice(task('moments'))['learned_preferences'] == []
    set_context(identity(tenant='b'))
    assert build_account_voice(task())['source_ids'] == []


@pytest.mark.real_tenancy

def test_wrong_active_store_cannot_be_requested_via_task(scoped, monkeypatch):
    import app.account_voice as module
    monkeypatch.setattr(module, 'read_creative_memory', lambda **_: pytest.fail('No reads across active store'))
    sheet = build_account_voice(task(store_id='s2'))
    assert sheet['memory_state'] == 'defaults' and not sheet['source_ids']


@pytest.mark.real_tenancy

def test_current_explicit_correction_overrides_earlier_tone_and_drops_unrelated_product_memory(scoped):
    from app.gtm_agent.repository import MEMORY
    register_resource('store', 's1', 's1', 'store', {'name': '本店'})
    now = time.time()
    for identifier, kind, text, key, superseded in [
        ('old', 'preference', '文案用很多emoji', 'emoji', 'new'),
        ('new', 'correction', '不用表情，语气干净直接', 'emoji', None),
        ('facts', 'preference', '设备已经支持999种软件', 'product', None),
    ]:
        register_resource(MEMORY, identifier, 's1', 'private', {'kind': kind, 'key': key, 'text': text,
            'quote': text, 'created_at': now, 'expires_at': now + 3600, 'superseded_by': superseded})
    sheet = build_account_voice(task('moments'))
    assert sheet['emoji_policy']['mode'] == 'none'
    assert len(sheet['learned_preferences']) == 1
    assert '999' not in str(sheet)
    assert sheet['source_ids'] == ['agent-memory:new']


@pytest.mark.real_tenancy

def test_feedback_notes_can_teach_voice_but_hidden_source_and_generated_story_cannot(scoped):
    register_resource('store', 's1', 's1', 'store', {'name': '本店'})
    source()
    register_resource('strategy_feedback', 'feedback', 's1', 'store',
        {'run_id': 'r1', 'event': 'edited', 'note': '段落短一点，保留自然口语。'})
    sheet = build_account_voice(task('moments'))
    assert sheet['learned_preferences'][0]['text'] == '段落短一点，保留自然口语。'
    assert sheet['source_ids'] == ['strategy-feedback:feedback']
    source('hidden', visibility='private')
    register_resource('strategy_feedback', 'private-feedback', 's1', 'store',
        {'run_id': 'hidden', 'event': 'edited', 'note': '私有稿幽默一点。'})
    set_context(identity(user='v', admin=True))
    assert '私有稿' not in str(build_account_voice(task('moments')))


@pytest.mark.real_tenancy
def test_platform_specific_feedback_does_not_force_other_channel_voice(scoped):
    register_resource('store', 's1', 's1', 'store', {'name': '本店'})
    source()  # A moments draft.
    record_creative_memory('r1', '朋友圈不用表情，语气短一点。')
    assert build_account_voice(task('moments'))['emoji_policy']['mode'] == 'none'
    assert build_account_voice(task('xiaohongshu'))['memory_state'] == 'defaults'
    assert build_account_voice(task('xiaohongshu'))['emoji_policy']['mode'] == 'contextual_unicode'

@pytest.mark.real_tenancy
def test_removing_source_run_removes_derived_voice_on_next_read(scoped):
    from sqlalchemy import delete
    from app.tenancy.database import db_connection, resources
    register_resource('store', 's1', 's1', 'store', {'name': '本店'})
    source()
    record_creative_memory('r1', '语气自然一点，标题要活泼。')
    assert build_account_voice(task('moments'))['learned_preferences']
    with db_connection() as conn:
        conn.execute(delete(resources).where(resources.c.kind == 'run', resources.c.id == 'r1'))
    assert build_account_voice(task('moments'))['source_ids'] == []
    assert build_account_voice(task('moments'))['memory_state'] == 'defaults'
