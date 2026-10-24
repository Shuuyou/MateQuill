"""Bounded public discovery connector for the opportunity agent.

Search excerpts are research leads, not verified product or customer facts.
No arbitrary page fetching, account cookies, or customer data is accepted.
"""
from __future__ import annotations

from collections import OrderedDict
from datetime import datetime, timezone
import html
from html.parser import HTMLParser
import ipaddress
import re
import time
from urllib.parse import urlsplit, urljoin
import xml.etree.ElementTree as ET

import httpx


class SignalSearchUnavailable(RuntimeError):
    pass


_cache: OrderedDict[str, tuple[float, list[dict]]] = OrderedDict()
DIRECTORY_URL = 'https://www.0576sj.com/stores'


def entity_matches(query: str, text: str) -> bool:
    """Require the named brand plus a specific place, never city-only overlap."""
    brands = [x for x in ('华为', '小米', 'OPPO', 'vivo', '荣耀') if x.lower() in query.lower()]
    places = [x for x in ('百脑汇', '东部软件园', '文三', '来福士', '金沙天街', '大悦城', '黄龙', '城西银泰', '滨江天街') if x in query]
    if not places:
        # An explicit quoted entity is a valid alternative to the bounded local
        # lexicon. Broad city / generic marketing queries intentionally return no
        # store-context evidence rather than an unrelated discovery result.
        places = re.findall(r'[“\"]([^”\"]{2,40})[”\"]', query)
    return bool(places) and all(x.lower() in text.lower() for x in brands) and any(x.lower() in text.lower() for x in places)


def directory_url(value: str) -> bool:
    """Only the fixed first-party directory and numeric store pages may fetch."""
    try:
        p = urlsplit(value)
        return (p.scheme == 'https' and p.netloc == 'www.0576sj.com'
            and not p.query and not p.fragment
            and bool(re.fullmatch(r'/stores(?:/[0-9]{1,8})?/?', p.path)))
    except ValueError:
        return False


class _StoreHTML(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.links: list[dict] = []
        self.parts: list[str] = []
        self.hidden = 0
        self.anchor: dict | None = None

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        if tag in ('script', 'style', 'noscript'):
            self.hidden += 1
        if self.hidden:
            return
        if tag == 'a':
            self.anchor = {'url': urljoin(DIRECTORY_URL, a.get('href', '')), 'parts': [a['title']] if a.get('title') else []}
        if tag == 'img' and self.anchor and a.get('alt'):
            self.anchor['parts'].append(a['alt'])

    def handle_endtag(self, tag):
        if tag in ('script', 'style', 'noscript'):
            self.hidden = max(0, self.hidden - 1)
        if tag == 'a' and self.anchor:
            self.links.append({'url': self.anchor['url'], 'title': _text(' '.join(self.anchor['parts']), 300)})
            self.anchor = None

    def handle_data(self, data):
        if self.hidden:
            return
        if data.strip():
            self.parts.append(data.strip())
            if self.anchor:
                self.anchor['parts'].append(data.strip())


def parse_directory(raw: str, *, query: str) -> list[dict]:
    if len(raw) > 800_000:
        raise SignalSearchUnavailable('directory_too_large')
    parser = _StoreHTML()
    parser.feed(raw)
    results, seen = [], set()
    for row in parser.links:
        if (row['url'] in seen or not directory_url(row['url'])
                or not re.fullmatch(r'/stores/[0-9]{1,8}/?', urlsplit(row['url']).path)
                or not entity_matches(query, row['title'])):
            continue
        seen.add(row['url'])
        results.append(row)
    return results[:3]


def parse_store_page(raw: str, *, url: str, query: str, observed_at: str) -> dict | None:
    if not directory_url(url) or not re.fullmatch(r'/stores/[0-9]{1,8}/?', urlsplit(url).path):
        raise ValueError('invalid_store_directory_url')
    if len(raw) > 800_000:
        raise SignalSearchUnavailable('store_page_too_large')
    parser = _StoreHTML()
    parser.feed(raw)
    # Choose the descriptive paragraph, not a navigation link: it must mention
    # the entity and an address cue. Keep the directly observed prose intact.
    paragraphs = [p for p in parser.parts if entity_matches(query, p) and '位于' in p]
    if not paragraphs:
        return None
    excerpt = _text(' '.join(paragraphs[:2]), 1200)
    title = next((p for p in parser.parts if entity_matches(query, p) and len(p) <= 100), '门店公开资料')
    return {'title': title, 'url': url, 'excerpt': excerpt, 'observed_at': observed_at,
            'published_at': None, 'provider': 'first_party_store_directory',
            'evidence_kind': 'public_store_context', 'query': query,
            'scope': '运营商公开门店位置与服务介绍；不推导实际客群、客流、库存或当前优惠。'}


async def _directory_get(client: httpx.AsyncClient, url: str) -> str:
    if not directory_url(url):
        raise ValueError('invalid_store_directory_url')
    async with client.stream('GET', url, headers={'User-Agent': 'MateQuill/1.0 PublicResearch'}) as response:
        response.raise_for_status()
        # Redirects are not followed; every network target stays on the fixed
        # approved host and path family. Size checks apply while streaming.
        if response.is_redirect:
            raise SignalSearchUnavailable('directory_redirect')
        raw = bytearray()
        async for chunk in response.aiter_bytes():
            raw.extend(chunk)
            if len(raw) > 800_000:
                raise SignalSearchUnavailable('directory_too_large')
        return bytes(raw).decode('utf-8', errors='replace')


async def search_store_directory(client: httpx.AsyncClient, query: str, observed_at: str) -> list[dict]:
    listing = await _directory_get(client, DIRECTORY_URL)
    links = parse_directory(listing, query=query)
    results = []
    for link in links:
        try:
            page = await _directory_get(client, link['url'])
            row = parse_store_page(page, url=link['url'], query=query, observed_at=observed_at)
            if row:
                results.append(row)
        except (httpx.HTTPError, SignalSearchUnavailable):
            continue
    return results


def _text(value: str, limit=1200) -> str:
    return re.sub(r'\s+', ' ', re.sub('<[^>]+>', '', html.unescape(value or ''))).strip()[:limit]


def public_url(value: str) -> bool:
    try:
        parts = urlsplit(value)
        host = parts.hostname or ''
        if parts.scheme not in ('http', 'https') or not host or parts.username or parts.password:
            return False
        if host == 'localhost' or '.' not in host or host.endswith(('.local', '.internal', '.test', '.localhost')):
            return False
        try:
            return ipaddress.ip_address(host).is_global
        except ValueError:
            return True
    except ValueError:
        return False


def parse_search_feed(raw: bytes, *, query: str, observed_at: str) -> list[dict]:
    if len(raw) > 500_000 or b'<!DOCTYPE' in raw.upper() or b'<!ENTITY' in raw.upper():
        raise SignalSearchUnavailable('invalid_search_feed')
    try:
        root = ET.fromstring(raw)
    except ET.ParseError:
        raise SignalSearchUnavailable('invalid_search_feed') from None
    results, seen = [], set()
    for item in root.findall('.//item'):
        url = (item.findtext('link') or '').strip()
        title = _text(item.findtext('title') or '', 300)
        excerpt = _text(item.findtext('description') or '')
        if not public_url(url) or not title or url in seen or not entity_matches(query, title + ' ' + excerpt):
            continue
        seen.add(url)
        results.append({'title': title, 'url': url,
            'excerpt': excerpt,
            'observed_at': observed_at, 'published_at': item.findtext('pubDate'),
            'provider': 'bing_public_rss', 'evidence_kind': 'public_search_excerpt',
            'query': query})
        if len(results) == 6:
            break
    return results


async def search_public_signals(query: str) -> list[dict]:
    query = _text(query, 301)
    if not 2 <= len(query) <= 300 or re.search(r'[\w.+-]+@[\w.-]+\.[a-zA-Z]{2,}|(?<!\d)1[3-9]\d{9}(?!\d)', query):
        raise ValueError('公开检索仅使用门店名称、位置、产品与公开活动关键词')
    cached = _cache.get(query)
    if cached and time.monotonic() - cached[0] < 1800:
        return [dict(row, cache_hit=True) for row in cached[1]]
    observed_at = datetime.now(timezone.utc).isoformat()
    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(9, connect=4), follow_redirects=False) as client:
            results = await search_store_directory(client, query, observed_at)
        if results:
            _cache[query] = (time.monotonic(), results)
            while len(_cache) > 128:
                _cache.popitem(last=False)
            return results
    except (httpx.HTTPError, SignalSearchUnavailable):
        pass
    # Public RSS is a replaceable discovery adapter, not a guaranteed platform
    # API. Failure remains visible to the planner and never manufactures results.
    for host in ('www.bing.com', 'cn.bing.com'):
        try:
            async with httpx.AsyncClient(timeout=httpx.Timeout(9, connect=4), follow_redirects=False) as client:
                async with client.stream('GET', f'https://{host}/search',
                    params={'q': query, 'format': 'rss'}, headers={'User-Agent': 'MateQuill/1.0 PublicResearch'}) as response:
                    response.raise_for_status()
                    raw = bytearray()
                    async for chunk in response.aiter_bytes():
                        raw.extend(chunk)
                        if len(raw) > 500_000:
                            raise SignalSearchUnavailable('search_feed_too_large')
            results = parse_search_feed(bytes(raw), query=query, observed_at=datetime.now(timezone.utc).isoformat())
            if not results:
                continue
            _cache[query] = (time.monotonic(), results)
            _cache.move_to_end(query)
            while len(_cache) > 128:
                _cache.popitem(last=False)
            return results
        except (httpx.HTTPError, SignalSearchUnavailable):
            continue
    return []
