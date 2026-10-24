"""Public catalog checkpointing and truthful navigation coverage without network."""
import json
from pathlib import Path

import httpx

import scripts.discover_vmall_catalog as module


def test_declared_nav_sources_are_found_inside_config_json_without_executing_strings():
    navigation = {'configInfo': json.dumps({'dataSourceList': [{'dataSourceCode': 'DA123'}]}),
                  'private': "__import__('os').system('bad')", 'other': {'dataSourceCode': 'DT123'}}
    assert module.data_source_codes(navigation) == ['DA123']


def test_full_reported_pagination_and_unreported_empty_tag_stay_distinct(tmp_path, monkeypatch):
    class Session:
        network_requests = 0
        def __init__(self, *args): pass
        def close(self): pass
        def request(self, method, path, payload):
            if path.endswith('getGlobalDataSource'):
                return {'configInfo': '{"dataSourceList":[{"dataSourceCode":"DA123"}]}'}, 'navigation-sha'
            if path.endswith('getProdInfoListWithPage'):
                return {'dataMap': {'DA123': {'totalRows': 3, 'dataInfos': [
                    {'dataType': 'tag', 'tagCode': '[{"code":"TAG1","cnName":"A"},{"code":"TAG2","cnName":"B"}]'},
                    {'dataType': 'prod', 'prdId': 500, 'skuCode': 'nav-sku', 'prdName': 'Direct navigation product'},
                ]}}}, 'group-sha'
            if payload['keyword'] == 'TAG2':
                return {'success': True, 'code': '0'}, 'unreported-empty-sha'
            page = int(payload['pageNum'])
            identifiers = range(1, 21) if page == 1 else [21]
            return {'pageNum': page, 'pageSize': 20, 'totalCount': 21,
                'resultList': [{'productId': identifier, 'skuCode': 'sku-'+str(identifier),
                                'shopName': 'Original actual field'} for identifier in identifiers]}, 'page-sha'

    monkeypatch.setattr(module, 'PublicSession', Session)
    result = module.discover(tmp_path)
    assert len(result['products']) == 21
    assert result['products'][0]['shopName'] == 'Original actual field'
    assert result['products'][0]['discovered_tags'] == ['TAG1']
    assert result['navigation_products'][0]['prdId'] == 500
    assert result['navigation_products'][0]['discovery_basis'] == 'public_navigation_direct_product_not_search_result'
    tags = result['coverage']['tags']
    assert tags[0]['status'] == 'complete' and tags[0]['pages_fetched'] == 2 and tags[0]['unique_products'] == 21
    assert tags[1]['status'] == 'empty_without_total' and tags[1]['total_reported'] is None
    assert result['coverage']['complete'] is False
    original_snapshot = result['snapshot_at']
    rerun = module.discover(tmp_path)
    assert rerun['snapshot_at'] == original_snapshot and len(rerun['products']) == 21


def test_cache_replay_avoids_network_and_never_serializes_headers_or_cookie_values(tmp_path, monkeypatch):
    sent = []
    def handle(request):
        sent.append(request.url)
        return httpx.Response(200, json={'success': True, 'code': '0', 'dataMap': {}})
    client = httpx.Client
    monkeypatch.setattr(module.httpx, 'Client', lambda **kwargs: client(transport=httpx.MockTransport(handle), **kwargs))
    session = module.PublicSession(tmp_path, 1)
    session.client.headers['CsrfToken'] = 'synthetic-csrf-never-persist'
    session.client.cookies.set('synthetic-cookie', 'synthetic-cookie-never-persist')
    first = session.request('GET', '/public-example', {'key': 'value'})
    assert session.request('GET', '/public-example', {'key': 'value'}) == first
    session.close()
    assert len(sent) == 1
    serialized = ''.join(path.read_text(encoding='utf-8') for path in tmp_path.rglob('*.json'))
    assert 'synthetic-csrf-never-persist' not in serialized and 'synthetic-cookie-never-persist' not in serialized


def test_atomic_checkpoint_retries_transient_windows_reader_lock(tmp_path, monkeypatch):
    original = Path.replace
    calls = []
    def replace(source, target):
        calls.append(1)
        if len(calls) < 3:
            raise PermissionError('synthetic Windows reader lock')
        return original(source, target)
    monkeypatch.setattr(Path, 'replace', replace)
    monkeypatch.setattr(module.time, 'sleep', lambda delay: None)
    module.write_json(tmp_path/'discovery.json', {'complete': False})
    assert len(calls) == 3 and json.loads((tmp_path/'discovery.json').read_text()) == {'complete': False}
