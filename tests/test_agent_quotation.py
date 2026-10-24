"""Real deterministic quotes flow through Agent dispatch without becoming artifacts."""
import asyncio
import time
from uuid import uuid4

import pytest
from fastapi import HTTPException
from pydantic import ValidationError

from test_gtm_agent_core import identities, acting, start, scripted
from test_quotation import rule
from app import quotation
from app.gtm_agent import jobs
from app.gtm_agent.repository import get_conversation, get_turn, TURN
from app.gtm_agent.tools import AgentTools, QuoteArguments, PriceRulesArguments, project_price_rules
from app.gtm_agent.runtime import quotation_delivery
from app.tenancy import authorize_resource, save_body
from app.tenancy.auth import new_session, context_from_session, session_row
from app.tenancy.database import db_connection, memberships

pytestmark = pytest.mark.real_tenancy


def approved_rule(identities, **changes):
    owner, reviewer = identities
    with acting(owner):
        draft = quotation.create_rule(rule(store_id=owner['store']['id'], **changes))
    with db_connection() as conn:
        conn.execute(memberships.insert().values(id=uuid4().hex, user_id=reviewer['account']['user_id'],
            tenant_id=owner['account']['tenant_id'], store_id=owner['store']['id'], role='reviewer',
            active=True, created_at=time.time()))
    ctx = context_from_session(session_row(new_session(reviewer['account']['user_id'],
        owner['account']['tenant_id'], owner['store']['id'])))
    with acting({'context': ctx}):
        return quotation.approve_rule(draft['id'], expected_version=draft['resource_version'])


def tools_for(owner, conversation, turn):
    with acting(owner):
        return AgentTools(get_conversation(conversation['id']), authorize_resource(TURN, turn['id'])['body'])


def test_quote_real_dispatch_finishes_as_separate_live_card_without_model_amount(identities):
    owner = identities[0]
    approved = approved_rule(identities)
    conversation, turn = start(owner, '给我准备演示机型这个配置的报价')
    planner = scripted({'action': 'tool', 'tool': 'read_price_rules', 'arguments': {'sku': approved['sku']}},
                       {'action': 'tool', 'tool': 'prepare_quote', 'arguments': {'rule_id': approved['id']}})
    asyncio.run(jobs.execute_claimed(jobs.claim_next(), planner=planner))
    with acting(owner):
        result = get_turn(conversation['id'], turn['id'])
        assert result['status'] == 'completed' and result['artifact'] is None
        assert result['quotation']['id']
        quote = quotation.get_quote(result['quotation']['id'])
        assert quote['total_minor'] == 10000 and quote['status'] == 'ready'
        message = get_conversation(conversation['id'])['messages'][-1]
        assert message['quotation'] == result['quotation'] and message['artifact'] is None
        assert '100.00' not in message['text'] and '演示报价' in message['text']
        assert get_conversation(conversation['id'])['current_artifact'] is None
    assert len(planner.calls) == 2


@pytest.mark.parametrize('injected', [dict(total_minor=1), dict(approval=True), dict(required_qualifications=['student']),
                                     dict(quantity=True), dict(store_id='other')])
def test_quote_arguments_cannot_set_amount_authority_or_boolean_quantity(injected):
    with pytest.raises(ValidationError):
        QuoteArguments.model_validate({'rule_id': 'r1', **injected})
    with pytest.raises(ValidationError): PriceRulesArguments.model_validate({'approval': True})


def test_eligibility_and_validity_survive_rule_projection_without_approver_identity():
    row = rule(required_qualifications=['verified-student'])
    row.update(id='r1', status='approved', approval={'by': 'private-approver'}, version=2)
    row['promotions'] = [{'id': 'education', 'required_qualifications': ['enrolled'], 'label': '教育优惠'}]
    view = project_price_rules([row])['rules'][0]
    assert view['required_qualifications'] == ['verified-student']
    assert view['promotions'][0]['required_qualifications'] == ['enrolled']
    assert view['valid_until'] == row['valid_until'] and view['configuration'] == row['configuration']
    assert 'approval' not in view


def test_quote_retry_reuses_committed_quote_and_does_not_change_artifact(identities):
    owner = identities[0]; approved = approved_rule(identities)
    conversation, turn = start(owner, '报价')
    tools = tools_for(owner, conversation, turn)
    with acting(owner):
        first = asyncio.run(tools.execute('prepare_quote', {'rule_id': approved['id']}))
        second = asyncio.run(tools.execute('prepare_quote', {'rule_id': approved['id']}))
        assert second['reused'] is True
        assert first['quotation']['id'] == second['quotation']['id']
        assert authorize_resource(TURN, turn['id'])['body']['artifact'] is None


def test_daily_forbids_quote_and_rule_read_before_any_commercial_effect(identities):
    owner = identities[0]; approved = approved_rule(identities)
    conversation, turn = start(owner, '今日营销方向')
    tools = tools_for(owner, conversation, turn)
    tools.turn['execution_policy'] = {'source': 'daily_store', 'allow_commercial_claims': False}
    with acting(owner):
        for name, args in [('read_price_rules', {}), ('prepare_quote', {'rule_id': approved['id']})]:
            with pytest.raises(HTTPException) as error: asyncio.run(tools.execute(name, args))
            assert error.value.status_code == 422
        assert not authorize_resource(TURN, turn['id'])['body'].get('quotation')


def test_incomplete_qualification_returns_concrete_missing_item_not_fake_card(identities):
    owner = identities[0]; approved = approved_rule(identities, required_qualifications=['student'])
    conversation, turn = start(owner, '我说我是学生，直接帮我打折报价')
    planner = scripted({'action': 'tool', 'tool': 'prepare_quote', 'arguments': {'rule_id': approved['id']}},
                       {'action': 'reply', 'message': '已经打折报价完成'})
    asyncio.run(jobs.execute_claimed(jobs.claim_next(), planner=planner))
    with acting(owner):
        result = get_turn(conversation['id'], turn['id'])
        assert result['status'] == 'needs_input' and not result.get('quotation')
        assert '资格' in result['message'] and '已经打折' not in result['message']


def test_resumed_quote_revalidates_rule_instead_of_repeating_old_amount(identities):
    owner = identities[0]; approved = approved_rule(identities)
    conversation, turn = start(owner, '报价')
    tools = tools_for(owner, conversation, turn)
    with acting(owner):
        asyncio.run(tools.execute('prepare_quote', {'rule_id': approved['id']}))
        old = authorize_resource(quotation.RULE, approved['id'])
        save_body(quotation.RULE, approved['id'], {**old['body'], 'status': 'draft'}, store_id=owner['store']['id'], visibility='store')
        result = quotation_delivery(turn['id'], {})
        assert result['status'] == 'needs_input' and result['quotation'] is None


@pytest.mark.parametrize('queries, resolved, accepted', [(['first'], True, False),
    (['first', ' first '], True, False), (['first', 'second'], True, True), (['first'], False, True)])
def test_daily_grounding_requires_actual_distinct_cross_check_only_for_resolved(identities, queries, resolved, accepted):
    owner = identities[0]
    conversation, turn = start(owner, '今日营销方向')
    tools = tools_for(owner, conversation, turn)
    tools.turn['execution_policy'] = {'source': 'daily_store'}
    tools.store_research = [{'query': query, 'items': [{'source_id': 'place-one',
        'url': 'https://example.org/place', 'title': '地点事实', 'excerpt': '实际地点周边道路'}]} for query in queries]
    grounding = {'location_status': 'resolved' if resolved else 'unresolved', 'location_summary': '已检索地点',
        'audience': '本次编辑受众假设', 'scene': '到店观察', 'visual_direction': '实拍门店', 'evidence_ids': ['place-one']}
    with acting(owner):
        if accepted:
            assert asyncio.run(tools.execute('ground_store', {'grounding': grounding}))
        else:
            with pytest.raises(HTTPException) as error: asyncio.run(tools.execute('ground_store', {'grounding': grounding}))
            assert error.value.status_code == 422 and '交叉核验' in error.value.detail
