"""Public place discovery with provenance, bounded fetches and replaceable providers.

Search excerpts are observations for the research agent, not measured audiences.
Only fixed provider endpoints are requested; source links are never fetched here.
"""
from __future__ import annotations

from collections import OrderedDict
from copy import deepcopy
from datetime import datetime, timezone
import hashlib
from html.parser import HTMLParser
import re
import time
from urllib.parse import urljoin, urlsplit

import httpx

from .public_signals import public_url

MAX_BYTES = 1_500_000
_cache: OrderedDict[str, tuple[float, dict]] = OrderedDict()


class DiscoveryUnavailable(RuntimeError):
    pass


def _clean(value, limit=1500):
    return re.sub(r'\s+', ' ', str(value or '')).strip()[:limit]


def _normalized(value):
    return re.sub(r'\s+', '', value).casefold()


def _search_result_url(value):
    """Exclude engine/AI answers that would recursively cite generated results.

    Original encyclopedia, news and publisher pages on other paths remain
    eligible; the search provider's outbound link redirect is not a new source.
    """
    parts = urlsplit(value)
    host = (parts.hostname or '').casefold()
    path = parts.path.rstrip('/') or '/'
    if host in {'ai.so.com', 'chat.baidu.com', 'chat.sogou.com'}:
        return True
    paths = {
        'www.so.com': {'/s', '/search'}, 'so.com': {'/s', '/search'},
        'www.sogou.com': {'/web', '/sogou'}, 'sogou.com': {'/web', '/sogou'},
        'www.bing.com': {'/search', '/chat', '/copilotsearch'},
        'cn.bing.com': {'/search', '/chat', '/copilotsearch'},
        'www.baidu.com': {'/s', '/sf'}, 'www.google.com': {'/search'},
    }
    return path in paths.get(host, set())


def query_entities(query):
    """Explicit quoted locations take priority; no city or place-name lexicon."""
    quoted = re.findall(r'[“"「]([^”"」]{2,100})[”"」]', query)
    if quoted:
        return quoted[:3]
    ignored = {'地址', '周边', '学校', '大学', '高校', '商圈', '住宅', '写字楼', '附近', '位置', '门店'}
    parts = [p for p in re.split(r'\s+', query) if len(p) >= 3 and p not in ignored]
    return sorted(parts, key=len, reverse=True)[:1]


class _Node:
    def __init__(self, tag='', attrs=(), parent=None):
        self.tag, self.attrs, self.parent = tag, dict(attrs), parent
        self.children = []

    def text(self):
        return ''.join(c if isinstance(c, str) else c.text() for c in self.children)

    def descendants(self):
        for child in self.children:
            if isinstance(child, _Node):
                yield child
                yield from child.descendants()


class _SearchHTML(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.root = self.current = _Node()

    def handle_starttag(self, tag, attrs):
        node = _Node(tag, attrs, self.current)
        self.current.children.append(node)
        if tag not in {'area', 'base', 'br', 'col', 'embed', 'hr', 'img', 'input', 'link', 'meta', 'param', 'source', 'track', 'wbr'}:
            self.current = node

    def handle_startendtag(self, tag, attrs):
        self.current.children.append(_Node(tag, attrs, self.current))

    def handle_endtag(self, tag):
        node = self.current
        while node.parent is not None:
            if node.tag == tag:
                self.current = node.parent
                break
            node = node.parent

    def handle_data(self, data):
        node = self.current
        while node.parent is not None:
            if node.tag in {'script', 'style', 'noscript'}:
                return
            node = node.parent
        self.current.children.append(data)


def parse_search_html(raw: str, *, query: str, provider: str, observed_at: str):
    if len(raw.encode('utf-8')) > MAX_BYTES:
        raise DiscoveryUnavailable('response_too_large')
    parser = _SearchHTML()
    parser.feed(raw)
    entities = [_normalized(value) for value in query_entities(query)]
    if not entities:
        return []
    results, seen = [], set()
    for node in parser.root.descendants():
        classes = node.attrs.get('class', '').split()
        if not (node.tag == 'li' and 'res-list' in classes or node.tag == 'div' and 'vrwrap' in classes):
            continue
        heading = next((n for n in node.descendants() if n.tag == 'h3'), None)
        anchor = next((n for n in heading.descendants() if n.tag == 'a'), None) if heading else None
        if anchor is None:
            continue
        title = _clean(heading.text(), 300)
        excerpt = _clean(node.text())
        if not any(entity in _normalized(title + excerpt) for entity in entities):
            continue
        base = 'https://www.so.com' if provider == 'so_public_search' else 'https://www.sogou.com'
        url = urljoin(base, anchor.attrs.get('data-mdurl') or anchor.attrs.get('href', ''))
        # Map oneboxes echo the requested entity in a generated heading even
        # when the actual POIs shown are unrelated. That is not place evidence.
        if urlsplit(url).hostname in {'map.360.cn', 'map.sogou.com'}:
            continue
        if _search_result_url(url):
            continue
        if not public_url(url) or url in seen or not title:
            continue
        seen.add(url)
        identifier = 'place-source:' + hashlib.sha256(url.encode()).hexdigest()[:20]
        results.append({'source_id': identifier, 'title': title, 'url': url, 'excerpt': excerpt,
            'provider': provider, 'observed_at': observed_at, 'published_at': None,
            'query': query, 'evidence_kind': 'public_search_excerpt'})
        if len(results) >= 8:
            break
    return results


async def _fetch(client, url, params):
    async with client.stream('GET', url, params=params,
            headers={'User-Agent': 'MateQuill/1.0 PublicPlaceResearch'}) as response:
        response.raise_for_status()
        if response.is_redirect:
            raise DiscoveryUnavailable('provider_redirect')
        chunks, size = [], 0
        async for chunk in response.aiter_bytes():
            size += len(chunk)
            if size > MAX_BYTES:
                raise DiscoveryUnavailable('response_too_large')
            chunks.append(chunk)
        return b''.join(chunks).decode('utf-8', errors='replace')


async def discover_place(query: str, *, profile: dict | None = None, use_cache=True, client=None) -> dict:
    """Find independently sourced place observations; never invent a POI or audience.

    ``profile`` may contain a public name/location to disambiguate the search.
    ``use_cache=False`` always performs a fresh network request for blind tests.
    A provider failure is recorded separately from a successful empty search.
    """
    query = _clean(query, 301)
    if not 2 <= len(query) <= 300 or re.search(r'[\w.+-]+@[\w.-]+\.[a-zA-Z]{2,}|(?<!\d)1[3-9]\d{9}(?!\d)', query):
        raise ValueError('请只使用公开门店名称、地点和周边关键词检索')
    public_profile = {k: _clean((profile or {}).get(k), 200) for k in ('name', 'location')}
    cache_key = query + '\0' + public_profile['location']
    cached = _cache.get(cache_key)
    if use_cache and cached and time.monotonic() - cached[0] < 1800:
        return {**deepcopy(cached[1]), 'cache_hit': True}
    observed_at = datetime.now(timezone.utc).isoformat()
    result = {'state': 'no_results', 'query': query, 'items': [], 'candidates': [],
        'observed_at': observed_at, 'cache_hit': False, 'providers': [], 'provider_errors': [],
        'resolution': 'evidence_only', 'profile': public_profile}
    if client is None:
        async with httpx.AsyncClient(timeout=httpx.Timeout(10, connect=4), follow_redirects=False) as owned:
            return await _discover(owned, query, cache_key, result)
    return await _discover(client, query, cache_key, result)


async def _discover(client, query, cache_key, result):
    # A Chinese place query should not depend on Bing RSS segmentation. The
    # providers below supply source-specific snippets and original URLs where
    # available. They are adapters, not a claim of licensed map API availability.
    network_query = re.sub(r'[“”"「」]', '', query)
    location = result['profile'].get('location', '')
    if location and _normalized(location) not in _normalized(network_query):
        network_query = _clean(network_query + ' ' + location, 500)
    result['search_query'] = network_query
    specs = [('so_public_search', 'https://www.so.com/s', {'q': network_query}),
             ('sogou_public_search', 'https://www.sogou.com/web', {'query': network_query, 'ie': 'utf8'})]
    for provider, endpoint, params in specs:
        try:
            raw = await _fetch(client, endpoint, params)
            rows = parse_search_html(raw, query=query, provider=provider, observed_at=result['observed_at'])
            for row in rows:
                row['search_query'] = network_query
            result['providers'].append({'provider': provider, 'state': 'found' if rows else 'no_results', 'count': len(rows)})
            result['items'].extend(rows)
            if rows:
                result['state'] = 'found'
                break
        except (httpx.HTTPError, DiscoveryUnavailable) as exc:
            # No query URLs, authorization data or provider response bodies in
            # surfaced errors; failures remain distinguishable from empty data.
            result['provider_errors'].append({'provider': provider, 'code': type(exc).__name__})
            result['providers'].append({'provider': provider, 'state': 'unavailable', 'count': 0})
    result['network_state'] = 'available' if any(p['state'] != 'unavailable' for p in result['providers']) else 'unavailable'
    if result['items']:
        _cache[cache_key] = (time.monotonic(), deepcopy(result))
        _cache.move_to_end(cache_key)
        while len(_cache) > 128:
            _cache.popitem(last=False)
    return result


async def search_place_evidence(query: str) -> list[dict]:
    """List adapter for callers using the existing search connector contract."""
    return (await discover_place(query))['items']
