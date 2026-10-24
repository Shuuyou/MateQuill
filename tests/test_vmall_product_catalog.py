"""Storefront discovery cannot silently become existing SKU knowledge."""
from copy import deepcopy
import sys
from types import ModuleType

from app import product_knowledge as knowledge


def install_reader(monkeypatch, rows):
    reader = ModuleType('app.vmall_catalog')
    reader.load_vmall_products = lambda: rows
    reader.vmall_summary = lambda row: {
        'id': row['catalog_key'], 'name': row['name'], 'spu_id': row['spu_id'],
        'sku_id': row['sku_id'], 'fact_count': len(row.get('facts') or []),
        'discovery_only': True, 'availability': row['availability'],
        'snapshot_at': row.get('snapshot_at', '')}
    monkeypatch.setitem(sys.modules, 'app.vmall_catalog', reader)
    return reader


def listing(key='vmall:spu1:sku1', **changes):
    return {'catalog_key': key, 'spu_id': 'spu1', 'sku_id': 'sku1', 'name': '同名设备',
        'facts': [], 'category': 'phone', 'availability': {'status': 'listed_on_sale',
        'stock_status': 'unknown'}, 'snapshot_at': '2026-10-24T20:00:00+08:00', **changes}


def test_same_name_listing_cannot_replace_or_borrow_knowledge_facts(monkeypatch):
    legacy = {'name': '同名设备', 'product': '同名设备', 'system': '已核对系统',
              'facts': [{'label': '内存', 'value': '旧版本规格', 'quote': '旧版本规格'}]}
    merchant = listing()
    before = deepcopy([legacy, merchant])
    monkeypatch.setattr(knowledge, 'load_products', lambda repository=None: [legacy])
    install_reader(monkeypatch, [merchant])
    rows = knowledge.load_product_catalog()
    assert len(rows) == 2
    assert rows[0]['facts'][0]['value'] == '旧版本规格'
    assert rows[1]['facts'] == [] and 'system' not in rows[1]
    assert knowledge.summary(rows[1])['discovery_only'] is True
    assert knowledge.summary(rows[1])['fact_count'] == 0
    assert [legacy, merchant] == before
    assert '_catalog_source' not in merchant


def test_different_skus_remain_distinct_while_duplicate_key_is_not_repeated(monkeypatch):
    one, two = listing(), listing('vmall:spu1:sku2', sku_id='sku2')
    monkeypatch.setattr(knowledge, 'load_products', lambda repository=None: [])
    install_reader(monkeypatch, [one, two, deepcopy(one), {'name': '缺少稳定身份'}])
    rows = knowledge.load_product_catalog()
    assert [row['sku_id'] for row in rows] == ['sku1', 'sku2']
    assert [knowledge.summary(row)['id'] for row in rows] == ['vmall:spu1:sku1', 'vmall:spu1:sku2']


def test_catalog_passes_scoped_repository_and_does_not_infer_stock_or_quote(monkeypatch):
    sentinel, received = object(), []
    monkeypatch.setattr(knowledge, 'load_products', lambda repository=None: received.append(repository) or [])
    install_reader(monkeypatch, [listing()])
    row = knowledge.summary(knowledge.load_product_catalog(sentinel)[0])
    assert received == [sentinel]
    assert row['availability']['stock_status'] == 'unknown'
    assert 'price' not in row and 'quotation' not in row


def test_even_listing_facts_are_not_joined_into_same_named_legacy_version(monkeypatch):
    old = {'name': '同名设备', 'facts': [{'id': 'old'}]}
    new = listing(facts=[{'id': 'new-specific-sku'}])
    monkeypatch.setattr(knowledge, 'load_products', lambda repository=None: [old])
    install_reader(monkeypatch, [new])
    rows = knowledge.load_product_catalog()
    assert rows[0]['facts'] == [{'id': 'old'}]
    assert rows[1]['facts'] == [{'id': 'new-specific-sku'}]


def test_explicit_catalog_route_uses_discovery_view_without_changing_generation_loader(monkeypatch):
    from app import product_routes
    monkeypatch.setattr(product_routes, 'load_product_catalog', lambda: [{'_catalog_source': 'vmall', **listing()}])
    install_reader(monkeypatch, [])
    result = product_routes.products()
    assert result['products'][0]['id'] == 'vmall:spu1:sku1'
    assert result['products'][0]['discovery_only'] is True


def test_legacy_summary_does_not_dispatch_from_a_user_claimed_catalog_key(monkeypatch):
    install_reader(monkeypatch, [])
    row = {'name': '已确认旧设备', 'catalog_key': 'user-claimed', 'facts': []}
    assert knowledge.summary(row)['id'].startswith('product-')
