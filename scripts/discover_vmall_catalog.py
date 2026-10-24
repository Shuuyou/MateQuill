"""Snapshot VMall's public navigation and paginate the tags it actually exposes.

Only the ordinary anonymous CSRF mechanism is used. Cookies/CSRF values stay in
memory. An access challenge stops collection; there is no login or bypass.
"""
from __future__ import annotations

import argparse
from datetime import datetime
import hashlib
import json
import math
from pathlib import Path
import re
import time
from zoneinfo import ZoneInfo

import httpx

API = 'https://openapi.vmall.com'
COMMON = {'lang': 'zh_CN', 'country': 'CN', 'portal': '1', 'version': '1'}
UA = 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/132.0.0.0 Safari/537.36'


def stamp():
    return datetime.now(ZoneInfo('Asia/Shanghai')).isoformat()


def write_json(path, value):
    temporary = path.with_suffix(path.suffix + '.partial')
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding='utf-8')
    # A concurrent Windows reader may hold the destination without delete
    # sharing for a few milliseconds. Keep the previous complete checkpoint.
    for attempt in range(7):
        try:
            temporary.replace(path)
            return
        except PermissionError:
            if attempt == 6:
                raise
            time.sleep(.05 * 2**attempt)


class DiscoveryError(RuntimeError):
    pass


class PublicSession:
    def __init__(self, destination, interval):
        self.raw = destination / 'requests'; self.raw.mkdir(exist_ok=True)
        self.interval = max(1.0, interval)
        self.previous = 0.0
        self.client = httpx.Client(timeout=35, trust_env=False, headers={
            'User-Agent': UA, 'Origin': 'https://www.vmall.com',
            'Referer': 'https://www.vmall.com/', 'X-Requested-With': 'XMLHttpRequest'})
        self.csrf_ready = False
        self.network_requests = 0

    def _wait(self):
        time.sleep(max(0, self.previous + self.interval - time.monotonic()))
        self.previous = time.monotonic()
        self.network_requests += 1

    def _csrf(self):
        self._wait()
        response = self.client.get(API + '/csrftoken.js')
        if response.status_code != 200:
            raise DiscoveryError('anonymous_csrf_http_' + str(response.status_code))
        found = re.search(r'csrftoken\s*=\s*["\x27]([^"\x27]+)', response.text)
        if not found:
            raise DiscoveryError('anonymous_csrf_unavailable_or_challenge')
        self.client.headers['CsrfToken'] = found.group(1)
        self.csrf_ready = True

    def request(self, method, path, payload):
        descriptor = {'method': method, 'path': path, 'payload': payload}
        digest = hashlib.sha256(json.dumps(descriptor, sort_keys=True, separators=(',', ':')).encode()).hexdigest()
        target = self.raw / (digest + '.json')
        if target.is_file():
            cached = json.loads(target.read_text(encoding='utf-8'))
            if cached.get('request') != descriptor:
                raise DiscoveryError('request_cache_mismatch')
            return cached['response'], cached['response_sha256']
        if method == 'POST' and not self.csrf_ready:
            self._csrf()
        self._wait()
        response = (self.client.get(API + path, params=payload) if method == 'GET'
                    else self.client.post(API + path, json=payload))
        if response.status_code != 200:
            # Do not log or cache authenticated headers, CSRF scripts or challenge bodies.
            raise DiscoveryError('public_api_http_' + str(response.status_code))
        try:
            value = response.json()
        except ValueError:
            raise DiscoveryError('public_api_non_json_or_challenge') from None
        if not isinstance(value, dict) or value.get('success') is not True or str(value.get('code')) != '0':
            raise DiscoveryError('public_api_unsuccessful_response')
        sha = hashlib.sha256(response.content).hexdigest()
        write_json(target, {'request': descriptor, 'fetched_at': stamp(),
                            'response_sha256': sha, 'response': value})
        return value, sha

    def close(self):
        self.client.close()


def data_source_codes(value):
    result = set()
    def walk(item):
        if isinstance(item, dict):
            code = item.get('dataSourceCode')
            if isinstance(code, str) and re.fullmatch(r'DA\d+', code):
                result.add(code)
            for key, child in item.items():
                if key == 'configInfo' and isinstance(child, str):
                    try: walk(json.loads(child))
                    except (ValueError, TypeError): pass
                else: walk(child)
        elif isinstance(item, list):
            for child in item: walk(child)
    walk(value)
    return sorted(result)


def merge_product(products, row, tag):
    identifier = row.get('productId')
    if isinstance(identifier, bool) or not str(identifier or '').isdigit():
        raise DiscoveryError('search_row_missing_product_id')
    key = str(identifier)
    if key not in products:
        products[key] = {**row, 'discovered_tags': [tag]}
    else:
        stored = products[key]
        if tag not in stored['discovered_tags']:
            stored['discovered_tags'].append(tag)
        # Search chooses one representative SKU. Keep differing actual results
        # rather than silently attributing another configuration to the first.
        if row.get('skuCode') != stored.get('skuCode'):
            variants = stored.setdefault('search_variants', [])
            if not any(value.get('skuCode') == row.get('skuCode') for value in variants):
                variants.append(row)


def discover(destination, *, interval=1.05, max_pages=200):
    destination.mkdir(parents=True, exist_ok=True)
    output = destination / 'discovery.json'
    session = PublicSession(destination, interval)
    previous = json.loads(output.read_text(encoding='utf-8')) if output.is_file() else {}
    products = {str(row['productId']): row for row in previous.get('products', [])}
    tags, navigation_products, nav_groups = {}, {}, {}
    result = {'snapshot_at': previous.get('snapshot_at') or stamp(), 'products': [], 'navigation_products': [],
        'coverage': {'tags': [], 'navigation_sha256': None, 'scope': 'public_navigation_tag_union',
                     'complete': False, 'global_index_available': False,
                     'global_index_note': 'Empty keyword returned no global count; coverage is the published navigation, not a proof of every VMall listing.'}}
    def save():
        result['products'] = list(products.values())
        result['navigation_products'] = list(navigation_products.values())
        result['coverage']['network_requests_this_run'] = session.network_requests
        result['updated_at'] = stamp()
        write_json(output, result)
    try:
        navigation, nav_sha = session.request('GET', '/sg/content/getGlobalDataSource',
                                              {**COMMON, 'subType': 'classifyPage'})
        result['coverage']['navigation_sha256'] = nav_sha
        write_json(destination / 'navigation.json', navigation)
        codes = data_source_codes(navigation)
        if not codes:
            raise DiscoveryError('navigation_data_sources_missing')
        for offset in range(0, len(codes), 30):
            response, _ = session.request('GET', '/mcp/content/getProdInfoListWithPage',
                {**COMMON, 'dataSourceList': json.dumps(codes[offset:offset+30], separators=(',', ':'))})
            groups = response.get('dataMap')
            if not isinstance(groups, dict) or set(codes[offset:offset+30]) - set(groups):
                raise DiscoveryError('navigation_data_source_response_incomplete')
            nav_groups.update(groups)
            for code, group in groups.items():
                for row in group.get('dataInfos', []):
                    if row.get('dataType') == 'tag':
                        try: linked_tags = json.loads(row.get('tagCode') or '[]')
                        except (ValueError, TypeError): raise DiscoveryError('invalid_navigation_tag_json') from None
                        for tag in linked_tags:
                            identifier = tag.get('code')
                            if not isinstance(identifier, str) or not re.fullmatch(r'TAG\d+', identifier):
                                raise DiscoveryError('invalid_navigation_tag_id')
                            tags.setdefault(identifier, {'tagid': identifier, 'names': [], 'data_sources': []})
                            if tag.get('cnName') and tag['cnName'] not in tags[identifier]['names']:
                                tags[identifier]['names'].append(tag['cnName'])
                            if code not in tags[identifier]['data_sources']:
                                tags[identifier]['data_sources'].append(code)
                    elif row.get('dataType') == 'prod' and str(row.get('prdId') or '').isdigit():
                        identifier = str(row['prdId'])
                        if identifier not in navigation_products:
                            navigation_products[identifier] = {**row, 'discovered_data_sources': [code],
                                'discovery_basis': 'public_navigation_direct_product_not_search_result'}
                        elif code not in navigation_products[identifier]['discovered_data_sources']:
                            navigation_products[identifier]['discovered_data_sources'].append(code)
            save()
        result['coverage']['data_sources'] = [{'code': code, 'configured_total': value.get('totalRows'),
            'visible_rows': len(value.get('dataInfos') or [])} for code, value in sorted(nav_groups.items())]
        result['coverage']['tags'] = [{**value, 'total_reported': None, 'pages_fetched': 0,
            'rows_seen': 0, 'unique_products': 0, 'status': 'pending', 'error': None}
            for _, value in sorted(tags.items())]
        save()
        for coverage in result['coverage']['tags']:
            seen, page_signatures = set(), set()
            try:
                page, expected = 1, None
                while expected is None or page <= max(1, math.ceil(expected / 20)):
                    if page > max_pages:
                        raise DiscoveryError('tag_page_budget_reached')
                    response, _ = session.request('POST', '/mcp/v1/search/queryPrd', {
                        'keyword': coverage['tagid'], 'pageSize': 20, 'pageNum': str(page),
                        'searchSortField': '0', 'searchSortType': 'desc', 'portal': '1',
                        'lang': 'zh-CN', 'country': 'CN', 'personalizeSearch': '2',
                        'searchFlag': '1', 'brandType': '0', 'tid': '', 'searchId': '', 'sid': ''})
                    total, rows = response.get('totalCount'), response.get('resultList')
                    if total is None and rows is None and page == 1:
                        raise DiscoveryError('empty_response_without_reported_total')
                    if (not isinstance(total, int) or isinstance(total, bool) or total < 0
                            or not isinstance(rows, list) or response.get('pageNum') != page
                            or response.get('pageSize') != 20):
                        raise DiscoveryError('invalid_search_pagination_contract')
                    if expected is not None and total != expected:
                        raise DiscoveryError('tag_total_changed_during_snapshot')
                    expected = total
                    coverage['total_reported'] = total
                    signature = tuple(str(row.get('productId')) for row in rows)
                    if rows and signature in page_signatures:
                        raise DiscoveryError('repeated_page_response')
                    page_signatures.add(signature)
                    if not rows and len(seen) < total:
                        raise DiscoveryError('empty_page_before_reported_total')
                    for row in rows:
                        merge_product(products, row, coverage['tagid'])
                        seen.add(str(row['productId']))
                    coverage.update(pages_fetched=page, rows_seen=coverage['rows_seen']+len(rows),
                                    unique_products=len(seen), status='fetching')
                    save()
                    page += 1
                if len(seen) != expected:
                    raise DiscoveryError('unique_products_do_not_match_tag_total')
                coverage['status'] = 'complete'
            except (DiscoveryError, httpx.HTTPError) as exc:
                code = str(exc) if isinstance(exc, DiscoveryError) else type(exc).__name__
                coverage.update(status='empty_without_total' if code == 'empty_response_without_reported_total' else 'incomplete', error=code)
                if isinstance(exc, DiscoveryError) and ('http_403' in str(exc) or 'challenge' in str(exc)):
                    result['coverage']['stop_reason'] = 'access_challenge_not_bypassed'
                    save(); break
            save()
            print(json.dumps({'tag': coverage['tagid'], 'status': coverage['status'],
                'total': coverage['total_reported'], 'unique': coverage['unique_products'],
                'products': len(products)}, ensure_ascii=True), flush=True)
        result['coverage']['complete'] = bool(tags) and all(
            item['status'] == 'complete' for item in result['coverage']['tags'])
    except (DiscoveryError, httpx.HTTPError) as exc:
        result['coverage']['error'] = str(exc) if isinstance(exc, DiscoveryError) else type(exc).__name__
    finally:
        save(); session.close()
    print(json.dumps({'output': str(output), 'complete': result['coverage']['complete'],
        'products': len(products), 'navigation_products': len(navigation_products),
        'tags': len(result['coverage']['tags'])}), flush=True)
    return result


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, default=Path('runtime/vmall-catalog-20261024'))
    parser.add_argument('--interval', type=float, default=1.05)
    parser.add_argument('--max-pages', type=int, default=200)
    options = parser.parse_args()
    discover(options.output, interval=options.interval, max_pages=options.max_pages)
