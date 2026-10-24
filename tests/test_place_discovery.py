import asyncio
import json

import httpx

from app.place_discovery import discover_place, parse_search_html, query_entities


NOW = '2026-10-22T11:00:00+00:00'
QUERY = '"星海广场" 海州市 周边'
HTML = '''<html><script>fake facts 星海广场</script>
<li class="res-list"><h3><a data-mdurl="https://public.example/place" href="https://www.so.com/link?token=example">星海<em>广场</em>交通介绍</a></h3>
<p>星海广场位于海州市大学路，与学生生活区相邻。</p></li>
<li class="res-list"><h3><a href="http://map.360.cn/?k=星海广场">在海州市搜索星海广场</a></h3><p>无关城市办公楼地址</p></li>
<li class="res-list"><h3><a href="http://127.0.0.1/private">星海广场</a></h3><p>内网</p></li>
<li class="res-list"><h3><a href="https://public.example/unrelated">别的广场</a></h3><p>其他地点</p></li>
<li class="res-list"><h3><a data-mdurl="https://public.example/place">星海广场重复结果</a></h3></li></html>'''


def test_public_result_parser_preserves_original_evidence_and_drops_query_echoes():
    rows = parse_search_html(HTML, query=QUERY, provider='so_public_search', observed_at=NOW)
    assert len(rows) == 1
    assert rows[0]['url'] == 'https://public.example/place'
    assert rows[0]['title'] == '星海广场交通介绍'
    assert '与学生生活区相邻' in rows[0]['excerpt']
    assert rows[0]['observed_at'] == NOW
    assert rows[0]['published_at'] is None
    assert rows[0]['source_id'].startswith('place-source:')
    assert 'fake facts' not in rows[0]['excerpt']


def test_unknown_place_names_do_not_need_a_lexicon():
    assert query_entities('"从未预置的星海广场" 周边') == ['从未预置的星海广场']
    assert query_entities('从未预置的星海广场 周边') == ['从未预置的星海广场']


def test_search_engine_answers_are_not_independent_sources():
    urls = ['https://ai.so.com/search/question', 'https://www.so.com/s?q=星海广场',
            'https://cn.bing.com/search?q=星海广场', 'https://www.sogou.com/web?query=星海广场',
            'https://www.baidu.com/s?wd=星海广场', 'https://www.google.com/search?q=星海广场']
    body = ''.join('<li class="res-list"><h3><a href="'+url+'">星海广场问答</a></h3><p>位置答案</p></li>' for url in urls)
    body += '<li class="res-list"><h3><a href="https://baike.so.com/doc/123.html">星海广场介绍</a></h3><p>文泽路</p></li>'
    rows = parse_search_html(body, query=QUERY, provider='so_public_search', observed_at=NOW)
    assert [row['url'] for row in rows] == ['https://baike.so.com/doc/123.html']


def test_cold_start_never_reuses_cached_evidence():
    requests = []
    def handle(request):
        requests.append(str(request.url))
        return httpx.Response(200, text=HTML)
    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as client:
            first = await discover_place(QUERY, use_cache=False, client=client)
            cached = await discover_place(QUERY, client=client)
            fresh = await discover_place(QUERY, use_cache=False, client=client)
            return first, cached, fresh
    first, cached, fresh = asyncio.run(run())
    assert first['state'] == fresh['state'] == 'found'
    assert cached['cache_hit'] is True
    assert fresh['cache_hit'] is False
    assert len(requests) == 2


def test_network_failure_is_distinct_from_empty_results_and_does_not_leak_url():
    def handle(request):
        raise httpx.ConnectTimeout('private URL or diagnostic text must not be surfaced', request=request)
    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as client:
            return await discover_place('"未记录地点" 地址', use_cache=False, client=client)
    result = asyncio.run(run())
    assert result['state'] == 'no_results'
    assert result['network_state'] == 'unavailable'
    assert len(result['provider_errors']) == 2
    assert result['items'] == []
    assert 'private URL' not in json.dumps(result)


def test_empty_or_blocked_provider_falls_back_without_claiming_success():
    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(lambda _: httpx.Response(200, text='<h1>captcha</h1>'))) as client:
            return await discover_place('"不存在的地点" 地址', use_cache=False, client=client)
    result = asyncio.run(run())
    assert result['state'] == 'no_results'
    assert len(result['providers']) == 2
    assert not result['items']


def test_provider_redirect_is_not_followed():
    visited = []
    def handle(request):
        visited.append(request.url.host)
        return httpx.Response(302, headers={'location': 'http://127.0.0.1/private'})
    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handle), follow_redirects=False) as client:
            return await discover_place('"尚未预置商场" 地址', use_cache=False, client=client)
    result = asyncio.run(run())
    assert visited == ['www.so.com', 'www.sogou.com']
    assert result['network_state'] == 'unavailable'
