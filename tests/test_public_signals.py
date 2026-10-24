import asyncio
import unittest

import httpx

from app.public_signals import (
    SignalSearchUnavailable, directory_url, entity_matches, parse_directory,
    parse_search_feed, parse_store_page, search_store_directory,
)


QUERY = '杭州百脑汇华为店 教工路23号'
NOW = '2026-10-22T10:00:00+08:00'
LISTING = '''<html><a href="/stores/24" title="杭州百脑汇华为店·hw1：教工路23号">店铺详情</a>
<a href="https://evil.example/stores/24" title="杭州百脑汇华为店">店铺详情</a>
<a href="/stores/25" title="杭州华商联华">详情</a></html>'''
PAGE = '''<html><script>杭州百脑汇华为店位于假地址</script><h1>杭州百脑汇华为店·hw1</h1>
<p>杭州百脑汇华为授权体验店位于杭州市西湖区教工路23号百脑汇1D-03。店内主营华为手机、平板、电脑。</p>
<p>门店地址</p></html>'''


class PublicSignalsTests(unittest.TestCase):
    def test_entity_requires_brand_and_specific_place(self):
        self.assertTrue(entity_matches(QUERY, '杭州百脑汇华为店'))
        self.assertFalse(entity_matches(QUERY, '杭州市人民政府旅游介绍'))
        self.assertFalse(entity_matches(QUERY, '杭州百脑汇苹果店'))
        self.assertFalse(entity_matches('杭州华为店', '杭州华为店'))

    def test_feed_drops_irrelevant_private_and_duplicate_results(self):
        xml = '''<rss><channel>
<item><title>杭州旅游</title><link>https://public.example/travel</link></item>
<item><title>杭州百脑汇华为店</title><link>http://127.0.0.1/x</link></item>
<item><title>杭州百脑汇华为店</title><link>https://www.0576sj.com/stores/24</link><description>教工路23号</description></item>
<item><title>杭州百脑汇华为店</title><link>https://www.0576sj.com/stores/24</link></item>
</channel></rss>'''.encode()
        rows = parse_search_feed(xml, query=QUERY, observed_at=NOW)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]['observed_at'], NOW)
        self.assertIsNone(rows[0]['published_at'])

    def test_feed_rejects_entity_declarations_and_large_data(self):
        for raw in (b'<!DOCTYPE rss><rss/>', b'<rss>', b' ' * 500001):
            with self.assertRaises(SignalSearchUnavailable):
                parse_search_feed(raw, query=QUERY, observed_at=NOW)

    def test_fixed_fetch_boundary(self):
        for url in ('https://www.0576sj.com/stores', 'https://www.0576sj.com/stores/24'):
            self.assertTrue(directory_url(url))
        for url in ('http://www.0576sj.com/stores/24', 'https://www.0576sj.com.evil.example/stores/24',
                    'https://www.0576sj.com@127.0.0.1/stores/24', 'https://www.0576sj.com/stores/../admin',
                    'https://www.0576sj.com/stores/24?next=http://127.0.0.1', 'https://www.0576sj.com/stores/%32%34',
                    'https://www.0576sj.com:443/stores/24', 'file:///stores/24'):
            self.assertFalse(directory_url(url), url)

    def test_directory_discovers_title_attribute_and_rejects_external(self):
        links = parse_directory(LISTING, query=QUERY)
        self.assertEqual([r['url'] for r in links], ['https://www.0576sj.com/stores/24'])

    def test_page_model_contains_observed_not_invented_published_time(self):
        row = parse_store_page(PAGE, url='https://www.0576sj.com/stores/24', query=QUERY, observed_at=NOW)
        self.assertEqual(row['provider'], 'first_party_store_directory')
        self.assertIn('教工路23号百脑汇1D-03', row['excerpt'])
        self.assertNotIn('假地址', row['excerpt'])
        self.assertIsNone(row['published_at'])
        self.assertEqual(row['observed_at'], NOW)
        self.assertEqual(row['evidence_kind'], 'public_store_context')
        self.assertIsNone(parse_store_page('<p>杭州百脑汇华为店地址：教工路23号</p>',
                          url='https://www.0576sj.com/stores/25', query=QUERY, observed_at=NOW))

    def test_http_discovery_then_detail_no_arbitrary_fetch(self):
        requests = []
        def handler(request):
            requests.append(str(request.url))
            return httpx.Response(200, text=LISTING if request.url.path == '/stores' else PAGE)
        async def run():
            async with httpx.AsyncClient(transport=httpx.MockTransport(handler), follow_redirects=False) as client:
                return await search_store_directory(client, QUERY, NOW)
        rows = asyncio.run(run())
        self.assertEqual(len(rows), 1)
        self.assertEqual(requests, ['https://www.0576sj.com/stores', 'https://www.0576sj.com/stores/24'])

    def test_redirect_does_not_follow_external_host(self):
        def handler(request):
            return httpx.Response(302, headers={'location': 'http://127.0.0.1/admin'})
        async def run():
            async with httpx.AsyncClient(transport=httpx.MockTransport(handler), follow_redirects=False) as client:
                await search_store_directory(client, QUERY, NOW)
        with self.assertRaises(httpx.HTTPStatusError):
            asyncio.run(run())


if __name__ == '__main__':
    unittest.main()
