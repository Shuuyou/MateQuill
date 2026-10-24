"""Price authority is approved data, never model arithmetic or a client checkbox."""
from contextlib import contextmanager
from copy import deepcopy
import time

import pytest
from fastapi import HTTPException
from pydantic import ValidationError

from app import quotation as q
from app.tenancy import TenantContext, set_context, reset_context
from app.tenancy.database import db_connection, tenants, users, clear_engine_cache

pytestmark = pytest.mark.real_tenancy


def rule(**changes):
    value = dict(store_id='one', product_name='演示机型', sku='DEMO-ONLY-128',
        configuration='演示配置 128GB', tax_included=True, unit_minor=10000,
        valid_from='2020-01-01T00:00:00+08:00', valid_until='2090-01-01T00:00:00+08:00',
        source_id='demo:approved-price-sheet', source_excerpt='仅用于自动化测试的演示价格资料，不是实际销售报价。',
        is_demo=True)
    return {**value, **changes}


def promo(identifier, amount=1000, **changes):
    return {**dict(id=identifier, label='演示优惠 '+identifier, kind='fixed', scope='quote',
        amount_minor=amount), **changes}


@contextmanager
def acting(user='maker', tenant='a', store='one', role='store_manager', all_stores=False):
    token = set_context(TenantContext(user, tenant, store_id=store, roles=(role,),
        store_ids=(store,), all_stores=all_stores,
        grants=((role, None if all_stores else store),)))
    try:
        yield
    finally:
        reset_context(token)


@pytest.fixture
def database(tmp_path, monkeypatch):
    monkeypatch.setenv('MATEQUILL_DATABASE_URL', 'sqlite:///' + (tmp_path/'quotes.sqlite').as_posix())
    clear_engine_cache()
    with db_connection() as connection:
        for tenant in ('a', 'b'):
            connection.execute(tenants.insert().values(id=tenant, name=tenant, created_at=time.time()))
        for user in ('maker', 'approver', 'staff', 'other'):
            connection.execute(users.insert().values(id=user, email=user+'@demo.test', name=user,
                password_hash='test-not-login', active=True, created_at=time.time()))
    yield
    clear_engine_cache()


def approved(payload=None):
    with acting():
        draft = q.create_rule(payload or rule())
    with acting('approver', role='reviewer'):
        return q.approve_rule(draft['id'], expected_version=draft['resource_version'])


@pytest.mark.parametrize('change', [dict(unit_minor=1.2), dict(unit_minor=True),
    dict(currency='USD'), dict(tax_included=False), dict(valid_from='2020-01-01'),
    dict(components=[dict(sku='demo', name='demo', quantity=1, unit_minor=1)]),
    dict(promotions=[promo('p'+str(index)) for index in range(13)])])
def test_money_and_contract_reject_ambiguous_or_unbounded_input(change):
    payload = rule()
    payload.update(change)
    with pytest.raises(ValidationError):
        q.RuleInput.model_validate(payload)


def test_exact_best_combination_beats_single_largest_and_requires_bilateral_permission():
    payload = rule(promotions=[promo('large', 2500),
        promo('a', 1500, mode='combinable', combinable_with=['b']),
        promo('b', 1500, mode='combinable', combinable_with=['a'])])
    result = q.optimize(payload, 1)
    assert result['total_minor'] == 7000
    assert result['promotion_ids'] == ['a', 'b']
    assert result['optimality']['evaluated_combinations'] == 8
    rejected = result['rejected_promotions'][0]
    assert rejected['promotion_id'] == 'large' and rejected['reason'] == 'not_in_best_valid_combination'
    assert rejected['conflicts'] == [{'with': name, 'reason': 'exclusive'} for name in ['a', 'b']]
    payload['promotions'][2]['combinable_with'] = []
    assert q.optimize(payload, 1)['promotion_ids'] == ['large']


def test_unit_order_caps_and_decimal_rounding_are_explicit():
    payload = rule(max_quantity=3, promotions=[promo('unit', 900, scope='unit', cap_minor=500, cap_scope='quote')])
    result = q.optimize(payload, 3)
    assert result['discount_minor'] == 500 and result['base_minor'] == 30000
    payload['promotions'][0]['cap_scope'] = 'unit'
    assert q.optimize(payload, 3)['discount_minor'] == 1500
    fractional = rule(unit_minor=105, promotions=[dict(id='rate', label='演示比例', kind='rate',
        scope='quote', basis_points=1000, rounding='half_up')])
    assert q.optimize(fractional, 1)['discount_minor'] == 11
    fractional['promotions'][0]['rounding'] = 'half_even'
    assert q.optimize(fractional, 1)['discount_minor'] == 10


def test_rule_approval_is_independent_version_fenced_and_staff_cannot_write(database):
    with acting():
        draft = q.create_rule(rule())
        assert q.prepare_quote(draft['id'])['status'] == 'needs_approved_rule'
        with pytest.raises(HTTPException) as error:
            q.approve_rule(draft['id'], expected_version=draft['resource_version'])
        assert error.value.status_code == 403
    with acting('staff', role='salesperson'):
        assert q.list_rules() == []
        with pytest.raises(HTTPException) as error:
            q.create_rule(rule())
        assert error.value.status_code == 403
    with acting('approver', role='reviewer'):
        with pytest.raises(HTTPException) as error:
            q.approve_rule(draft['id'], expected_version=999)
        assert error.value.status_code == 409
        result = q.approve_rule(draft['id'], expected_version=draft['resource_version'])
        assert result['status'] == 'approved'
    with acting('staff', role='salesperson'):
        quote = q.prepare_quote(draft['id'])
        assert quote['total_minor'] == 10000 and quote['is_demo'] is True
        assert quote['rule_resource_version'] == result['resource_version']


def test_qualification_requires_matching_approved_evidence_and_revocation_invalidates(database):
    contract = approved(rule(required_qualifications=['demo_student']))
    with acting('staff', role='salesperson'):
        assert q.prepare_quote(contract['id'], subject_id='customer-opaque')['status'] == 'needs_qualification'
        with pytest.raises(ValidationError):
            q.QuoteInput(rule_id=contract['id'], is_student=True)
        evidence = q.create_qualification(dict(store_id='one', subject_id='customer-opaque',
            code='demo_student', source_id='demo:proof', source_excerpt='演示用人工核验记录，非真实个人资料。',
            valid_from='2020-01-01T00:00:00+08:00', valid_until='2090-01-01T00:00:00+08:00'))
        assert q.prepare_quote(contract['id'], subject_id='customer-opaque')['status'] == 'needs_qualification'
    with acting('approver', role='reviewer'):
        evidence = q.approve_qualification(evidence['id'], expected_version=evidence['resource_version'])
    with acting('staff', role='salesperson'):
        assert q.prepare_quote(contract['id'], subject_id='someone-else')['status'] == 'needs_qualification'
        quote = q.prepare_quote(contract['id'], subject_id='customer-opaque')
        assert quote['status'] == 'ready' and len(quote['qualification_versions']) == 1
    with acting('approver', role='reviewer'):
        q.revoke_qualification(evidence['id'], expected_version=evidence['resource_version'])
    with acting('staff', role='salesperson'):
        current = q.get_quote(quote['id'])
        assert current['status'] == 'invalidated' and current['total_minor'] is None
        assert current['base_minor'] is None and current['trace'] == []
        assert 'qualification_changed' in current['invalid_reasons']


def test_rules_and_quotes_do_not_cross_store_or_tenant_and_updates_invalidate(database):
    contract = approved()
    with acting('staff', role='salesperson'):
        quote = q.prepare_quote(contract['id'])
    for tenant, store in [('a', 'two'), ('b', 'one')]:
        with acting('other', tenant=tenant, store=store, role='salesperson'):
            assert q.list_rules() == []
            with pytest.raises(HTTPException) as error:
                q.get_quote(quote['id'])
            assert error.value.status_code == 404
    with acting():
        changed = rule()
        changed['unit_minor'] = 9500
        q.update_rule(contract['id'], changed, expected_version=contract['resource_version'])
    with acting('staff', role='salesperson'):
        current = q.get_quote(quote['id'])
        assert current['status'] == 'invalidated' and current['total_minor'] is None
        assert 'rule_changed' in current['invalid_reasons']


def test_discount_qualification_does_not_block_base_price_and_no_money_is_mutated():
    payload = rule(promotions=[promo('student', 1000, required_qualifications=['student'])])
    untouched = deepcopy(payload)
    result = q.optimize(payload, 1)
    assert result['total_minor'] == 10000
    assert result['rejected_promotions'][0]['reason'] == 'qualification_required'
    assert q.optimize(payload, 1, ['student'])['total_minor'] == 9000
    assert payload == untouched


def test_bundle_sum_and_fees_remain_consistent_and_amount_overflow_is_rejected():
    payload = rule(components=[dict(sku='demo-a', name='演示主机', quantity=1, unit_minor=8000),
        dict(sku='demo-b', name='演示配件', quantity=2, unit_minor=1000)], fees_minor=300,
        promotions=[promo('all', 10000)])
    assert q.optimize(payload, 1)['total_minor'] == 300
    large = rule(unit_minor=q.MAX_MINOR, max_quantity=2)
    with pytest.raises(HTTPException):
        q.optimize(large, 2)


def test_fixed_priority_base_and_remaining_have_reproducible_trace():
    payload = rule(promotions=[promo('fixed', 1000, mode='combinable', combinable_with=['rate'], priority=1),
        dict(id='rate', label='演示九折', kind='rate', scope='quote', basis_points=1000,
            basis='remaining', mode='combinable', combinable_with=['fixed'], priority=2)])
    result = q.optimize(payload, 1)
    assert result['total_minor'] == 8100
    assert [step['deduction_minor'] for step in result['trace']] == [1000, 900]
    payload['promotions'][1]['basis'] = 'base'
    assert q.optimize(payload, 1)['total_minor'] == 8000


def test_same_exclusive_group_and_over_discount_never_form_a_negative_quote():
    payload = rule(promotions=[promo('a', 6000, mode='combinable', combinable_with=['b'], exclusive_group='campaign'),
        promo('b', 5000, mode='combinable', combinable_with=['a'], exclusive_group='campaign')])
    assert q.optimize(payload, 1)['total_minor'] == 4000
    payload['promotions'][0]['exclusive_group'] = ''
    payload['promotions'][1]['exclusive_group'] = ''
    result = q.optimize(payload, 1)
    assert result['total_minor'] == 4000
    assert result['optimality']['rejected_combinations'] == 1


def test_quote_expiry_and_rule_revocation_hide_all_old_amounts(database, monkeypatch):
    contract = approved()
    with acting('staff', role='salesperson'):
        quote = q.prepare_quote(contract['id'])
        with monkeypatch.context() as clock:
            clock.setattr(q.time, 'time', lambda: q._instant(quote['valid_until'])+1)
            expired = q.get_quote(quote['id'])
            assert expired['status'] == 'invalidated' and expired['total_minor'] is None
            assert 'quote_expired' in expired['invalid_reasons']
    with acting():
        q.revoke_rule(contract['id'], expected_version=contract['resource_version'])
    with acting('staff', role='salesperson'):
        assert q.get_quote(quote['id'])['status'] == 'invalidated'
        assert q.prepare_quote(contract['id'])['status'] == 'needs_approved_rule'


def test_qualification_creator_cannot_self_approve_even_if_admin(database):
    with acting(role='admin', all_stores=True):
        evidence = q.create_qualification(dict(store_id='one', subject_id='opaque',
            code='demo', source_id='demo:source', source_excerpt='测试专用资格依据，非真实客户。',
            valid_from='2020-01-01T00:00:00+08:00', valid_until='2090-01-01T00:00:00+08:00'))
        with pytest.raises(HTTPException) as error:
            q.approve_qualification(evidence['id'], expected_version=evidence['resource_version'])
        assert error.value.status_code == 403


def test_public_prepare_schema_refuses_client_amount_and_qualification_assertions(database):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from app.quotation_routes import router
    api = FastAPI()
    api.include_router(router)
    contract = approved()
    with acting('staff', role='salesperson'), TestClient(api) as client:
        assert client.post('/api/quotations/prepare', json=dict(rule_id=contract['id'], total_minor=1)).status_code == 422
        assert client.post('/api/quotations/prepare', json=dict(rule_id=contract['id'], eligible=True)).status_code == 422
        response = client.post('/api/quotations/prepare', json=dict(rule_id=contract['id'], quantity=1))
        assert response.status_code == 200 and response.json()['total_minor'] == 10000
        loaded = client.get('/api/quotations/'+response.json()['id'])
        assert loaded.status_code == 200 and loaded.json()['is_demo'] is True


def test_main_application_router_and_origin_guard_end_to_end(database, monkeypatch):
    """Exercise the registered app router with real pricing data in a temp DB."""
    from fastapi.testclient import TestClient
    from app.api import app
    from app.tenancy import get_context
    identity = [None]
    monkeypatch.setattr('app.tenancy.auth.resolve_request_context', lambda request: identity[0])
    client = TestClient(app)
    headers = {'Origin': 'http://testserver', 'X-MateQuill-Workspace': 'a:one'}
    assert client.get('/api/quotations/rules').status_code == 401
    with acting():
        identity[0] = get_context()
        assert client.post('/api/quotations/rules', json=rule()).status_code == 403
        response = client.post('/api/quotations/rules', json=rule(), headers=headers)
        assert response.status_code == 200
        draft = response.json()
    with acting('approver', role='reviewer'):
        identity[0] = get_context()
        response = client.post('/api/quotations/rules/'+draft['id']+'/approve',
            json={'expected_version': draft['resource_version']}, headers=headers)
        assert response.status_code == 200
    with acting('staff', role='salesperson'):
        identity[0] = get_context()
        response = client.post('/api/quotations/prepare', json={'rule_id': draft['id']}, headers=headers)
        assert response.status_code == 200
        value = response.json()
        assert value['status'] == 'ready' and value['is_demo'] is True
        loaded = client.get('/api/quotations/'+value['id'], headers=headers)
        assert loaded.json()['total_minor'] == 10000
        assert loaded.headers['cache-control'] == 'private, no-store'
    with acting('other', store='two', role='salesperson'):
        identity[0] = get_context()
        assert client.get('/api/quotations/'+value['id']).status_code == 404
