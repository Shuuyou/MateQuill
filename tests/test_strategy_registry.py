from copy import deepcopy
from datetime import datetime, timezone
import json

from fastapi import HTTPException
import pytest
from pydantic import ValidationError

from app.strategy_registry import (BUILTIN_PACKS, StrategyPack, StrategyFeedback,
    resolve_strategy, validate_snapshot, freeze_strategy_snapshot,
    record_strategy_feedback, save_strategy_pack, list_strategy_packs)
from app.creative_connectors import get_connector


def test_strategy_routes_identity_materials_and_objective():
    ctx = {'platform': 'xiaohongshu', 'publisher_identity': 'store'}
    assert resolve_strategy(ctx)['pack']['id'] == 'xhs.curiosity_payoff'
    assert resolve_strategy({**ctx, 'materials': ['real_photo']})['pack']['id'] == 'retail.real_demo'
    assert resolve_strategy({**ctx, 'publisher_identity': 'salesperson'})['pack']['id'] == 'xhs.curiosity_payoff'
    assert resolve_strategy({**ctx, 'objective': '华为生态协同'})['pack']['id'] == 'retail.ecosystem_task'
    assert resolve_strategy({**ctx, 'publisher_identity': 'brand'})['pack']['id'] == 'retail.brand_launch'


def test_expired_and_incompatible_explicit_strategy_are_excluded():
    pack = deepcopy(BUILTIN_PACKS[0])
    pack['expires_at'] = '2026-10-21T00:00:00+08:00'
    with pytest.raises(ValueError, match='eligible'):
        resolve_strategy({'platform': 'xiaohongshu'}, packs=[pack],
                         now=datetime(2026, 10, 22, tzinfo=timezone.utc))
    with pytest.raises(ValueError):
        resolve_strategy({'platform': 'wechat', 'publisher_identity': 'store'}, requested_id='retail.brand_launch')
    pack['expires_at'] = '2026-10-21T00:00:00'
    with pytest.raises(ValidationError): StrategyPack.model_validate(pack)


def test_snapshot_deepcopy_and_hash_prevent_silent_mutation():
    context = {'platform': 'moments', 'publisher_identity': 'salesperson', 'store_context': {'voice': '自然'}}
    snapshot = resolve_strategy(context)
    context['store_context']['voice'] = 'changed'
    assert snapshot['context']['store_context']['voice'] == '自然'
    assert validate_snapshot(snapshot) == snapshot
    snapshot['pack']['recipe']['narrative'].append('changed')
    with pytest.raises(ValueError, match='integrity'): validate_snapshot(snapshot)


def test_creative_brief_consumes_campaign_context_and_recipe():
    task = {'platform': 'xiaohongshu', 'requirements': json.dumps({'campaign': {'publisher_identity': 'brand'}})}
    brief = get_connector('xiaohongshu').build_brief(task, {'assets': []})
    assert brief['strategy_snapshot']['pack']['id'] == 'retail.brand_launch'
    assert brief['creative_playbook']['strategy_id'] == 'retail.brand_launch'
    assert brief['creative_playbook']['narrative_sequence']
    assert '官方产品视觉' in brief['creative_playbook']['visual_sequence']
    # An authoritative execution context takes precedence over inherited prompt text.
    brief = get_connector('xiaohongshu').build_brief(task, {'assets': []}, {'strategy_context': {'publisher_identity': 'store'}})
    assert brief['strategy_snapshot']['pack']['id'] == 'xhs.curiosity_payoff'


def test_frozen_strategy_reused_and_cannot_cross_product_or_identity():
    from app.strategy_registry import strategy_for_task
    task = {'platform': 'xiaohongshu', 'product': 'MateBook', 'store_id': 's1'}
    snapshot = strategy_for_task(task)
    assert strategy_for_task(task, strategy={'strategy_snapshot': snapshot}) == snapshot
    with pytest.raises(ValueError, match='product'):
        strategy_for_task({**task, 'product': 'MatePad'}, strategy={'strategy_snapshot': snapshot})
    with pytest.raises(ValueError, match='publisher_identity'):
        strategy_for_task(task, strategy={'strategy_snapshot': snapshot, 'strategy_context': {'publisher_identity': 'brand'}})
    with pytest.raises(ValueError, match='ecosystem_products'):
        strategy_for_task({**task, 'ecosystem_products': ['MatePad']}, strategy={'strategy_snapshot': snapshot})


def test_strategy_is_platform_specific_without_fabricating_customer_history():
    xhs = resolve_strategy({'platform': 'xiaohongshu', 'publisher_identity': 'store'})
    assert '兑现' in ''.join(xhs['pack']['recipe']['narrative'])
    moments = resolve_strategy({'platform': 'moments', 'publisher_identity': 'salesperson', 'audience': '已购熟客'})
    assert moments['pack']['id'] == 'moments.familiar_answer'
    assert '不重复促销' in ''.join(moments['pack']['recipe']['narrative'])
    stranger = resolve_strategy({'platform': 'moments', 'publisher_identity': 'salesperson', 'audience': '新客户'})
    assert stranger['pack']['id'] != 'moments.familiar_answer'
    assert not any('product_facts' in pack['recipe'] for pack in BUILTIN_PACKS)


def test_feedback_does_not_turn_adoption_or_export_into_publication():
    assert StrategyFeedback(event='adopted').publication_id == ''
    with pytest.raises(ValidationError): StrategyFeedback(event='published')
    with pytest.raises(ValidationError): StrategyFeedback(event='exported', impressions=200)
    with pytest.raises(ValidationError): StrategyFeedback(event='performance', publication_id='x', impressions=-1)


@pytest.fixture
def scoped(monkeypatch, tmp_path):
    from app.tenancy.context import TenantContext, set_context, reset_context
    from app.tenancy.database import db_connection, tenants, users
    monkeypatch.setenv('MATEQUILL_DATABASE_URL', 'sqlite:///' + (tmp_path / 'strategies.sqlite3').as_posix())
    with db_connection() as connection:
        connection.execute(tenants.insert(), [{'id': 'a', 'name': 'A', 'created_at': 1}, {'id': 'b', 'name': 'B', 'created_at': 1}])
        connection.execute(users.insert(), [{'id': 'u', 'email': 'u@example.com', 'name': 'U', 'password_hash': 'x', 'created_at': 1, 'active': True}])
    token = set_context(TenantContext(user_id='u', tenant_id='a', store_id='s1', roles=('admin',), all_stores=True))
    try:
        yield set_context, TenantContext
    finally:
        reset_context(token)


@pytest.mark.real_tenancy
def test_snapshots_feedback_and_packs_are_tenant_isolated(scoped):
    from app.tenancy.resources import register_resource, load_body
    set_context, Context = scoped
    register_resource('run', 'r1', 's1', 'private', {'_poster_task': {'assignment_id': 'a1'}})
    register_resource('channel_assignment', 'a1', 's1', 'store', {})
    snapshot = freeze_strategy_snapshot(resolve_strategy({'platform': 'moments', 'publisher_identity': 'store'}), run_id='r1', assignment_id='a1')
    assert freeze_strategy_snapshot(snapshot['body']['snapshot'], run_id='r1', assignment_id='a1')['id'] == snapshot['id']
    event = record_strategy_feedback(snapshot['id'], {'event': 'adopted', 'active_input_seconds': 4, 'source': 'authorized_platform'})
    assert load_body('strategy_feedback', event['id'])['event'] == 'adopted'
    assert load_body('strategy_feedback', event['id'])['source'] == 'user_report'
    register_resource('channel_publication', 'p1', 's1', 'store', {'run_id': 'other'})
    with pytest.raises(HTTPException):
        record_strategy_feedback(snapshot['id'], {'event': 'published', 'publication_id': 'p1'})
    pack = deepcopy(BUILTIN_PACKS[0]); pack['id'] = 'tenant.custom'
    save_strategy_pack(pack)
    assert any(p['id'] == 'tenant.custom' for p in list_strategy_packs())
    pack['title'] = '改写同版本'
    with pytest.raises(HTTPException) as conflict: save_strategy_pack(pack)
    assert conflict.value.status_code == 409
    set_context(Context(user_id='u', tenant_id='b', store_id='s2', roles=('admin',), all_stores=True))
    assert not any(p['id'] == 'tenant.custom' for p in list_strategy_packs())
    with pytest.raises(HTTPException): record_strategy_feedback(snapshot['id'], {'event': 'adopted'})


@pytest.mark.real_tenancy
def test_assignment_store_and_run_link_are_checked(scoped):
    from app.tenancy.resources import register_resource
    register_resource('run', 'r2', 's1', 'private', {'task': {'assignment_id': 'different'}})
    register_resource('channel_assignment', 'a2', 's1', 'store', {})
    snapshot = resolve_strategy({'platform': 'moments'})
    with pytest.raises(HTTPException): freeze_strategy_snapshot(snapshot, run_id='r2', assignment_id='a2')
    with pytest.raises(HTTPException): freeze_strategy_snapshot(snapshot, run_id='r2', store_id='s2')
