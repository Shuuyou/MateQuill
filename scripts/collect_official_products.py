"""Refresh Huawei public product facts; no login, price API or access-control bypass.

Usage: python scripts/collect_official_products.py [--expand]
Raw responses are private runtime snapshots. Source URLs and hashes are retained.
"""
from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
from datetime import datetime
from pathlib import Path
import re
import sys
from urllib.parse import urlparse
from urllib.parse import urljoin

import httpx

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from app.official_catalog import extract_specification, extract_product_conditions
from app.facts import extract_product_facts


def discover():
    products = []
    for path in (ROOT/'research'/'knowledge').rglob('产品事实.md'):
        if 'P1-产品参数库' not in str(path):
            continue
        text = path.read_text(encoding='utf-8-sig')
        name = re.search(r'^# (.+?) - 产品事实', text).group(1)
        product_url = re.search(r'产品页：(https://\S+)', text).group(1)
        spec_url = re.search(r'规格页：(https://\S+)', text).group(1)
        products.append({'name': name, 'product': name, 'product_url': product_url, 'spec_url': spec_url,
                         'scope': 'competition_p1', 'generation': '2024' if '2024' in spec_url else 'exact_url_variant'})
    return products


async def collect(products):
    snapshot = ROOT/'runtime'/'official-pages'
    snapshot.mkdir(parents=True, exist_ok=True)
    semaphore = asyncio.Semaphore(3)
    async with httpx.AsyncClient(timeout=35, follow_redirects=False, headers={'User-Agent': 'HuaweiPCResearch/1.0 (public product specification collection)'}) as client:
        async def fetch(product):
            async with semaphore:
                item = dict(product, captured_at=datetime.now().astimezone().isoformat(), sources=[], facts=[], status='unavailable')
                bodies = {}
                for role in ('spec', 'product'):
                    url = product[role+'_url']
                    if urlparse(url).hostname != 'consumer.huawei.com':
                        continue
                    try:
                        response = await client.get(url)
                        response.raise_for_status()
                        if response.is_redirect:
                            raise ValueError('redirect requires explicit source verification')
                        raw = response.content
                        digest = hashlib.sha256(raw).hexdigest()
                        file = snapshot/(digest+'.html')
                        file.write_bytes(raw)
                        bodies[role] = raw.decode('utf-8', errors='replace')
                        item['sources'].append({'role': role, 'url': url, 'captured_at': item['captured_at'], 'sha256': digest, 'local_path': str(file.relative_to(ROOT)).replace('\\','/')})
                    except (httpx.HTTPError, ValueError) as exc:
                        item.setdefault('errors', []).append({'url': url, 'error': type(exc).__name__+': '+str(exc)[:180]})
                if 'spec' in bodies:
                    extracted = extract_specification(bodies['spec'], product['name'])
                    item.update(extracted)
                    sid = 'official-'+hashlib.sha256(product['spec_url'].encode()).hexdigest()[:16]
                    facts = extract_product_facts(extracted['text'], product['name'], sid, max_facts=100)
                    item['facts'] = [dict(f.model_dump(), source_url=product['spec_url']) for f in facts]
                    os_section = next((s['text'] for s in extracted['sections'] if s['label']=='操作系统'), '')
                    item['system'] = os_section
                    item['status'] = 'collected' if item['facts'] else 'no_structured_specification'
                    item['sha256'] = next(s['sha256'] for s in item['sources'] if s['role']=='spec')
                if 'product' in bodies:
                    item['marketing_conditions'] = extract_product_conditions(bodies['product'])
                    item['marketing_conditions_source_url'] = product['product_url']
                print(product['name']+': '+item['status']+', '+str(len(item['facts']))+' facts', flush=True)
                return item
        return await asyncio.gather(*(fetch(p) for p in products))


async def discover_additional(products):
    """Expand only links actually published on Huawei's current category pages."""
    existing = {p['product_url'] for p in products}
    from app.official_catalog import _Tree, _clean
    async with httpx.AsyncClient(timeout=30, follow_redirects=False) as client:
        for category in ('https://consumer.huawei.com/cn/laptops/', 'https://consumer.huawei.com/cn/harmonyos-computer/'):
            try:
                response = await client.get(category)
                response.raise_for_status()
                tree = _Tree()
                tree.feed(response.content.decode('utf-8', errors='replace'))
                for node in tree.root.walk():
                    href = node.attrs.get('href', '')
                    url = urljoin(category, href).split('?')[0].split('#')[0]
                    if not re.fullmatch(r'https://consumer\.huawei\.com/cn/(?:laptops|harmonyos-computer)/[a-z0-9-]+/', url) or url in existing:
                        continue
                    if any(term in url for term in ('accessories','compare','select','all-products')):
                        continue
                    name = _clean(node.text()).split('\n')[0] or url.rstrip('/').split('/')[-1]
                    if not re.search(r'MateBook|MateStation', name, re.I):
                        continue
                    existing.add(url)
                    products.append({'name': name, 'product': name, 'product_url': url, 'spec_url': url+'specs/',
                                     'scope': 'official_category_discovery', 'generation': 'exact_url_variant', 'discovered_at': category})
            except httpx.HTTPError:
                continue
    return products


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--expand', action='store_true', help='Also collect product links actually discovered on current official category pages')
    args = parser.parse_args()
    products = discover()
    if args.expand:
        products = asyncio.run(discover_additional(products))
    items = asyncio.run(collect(products))
    output = ROOT/'data'/'official_products.json'
    output.parent.mkdir(exist_ok=True)
    output.write_text(json.dumps({'schema_version': 1, 'captured_at': datetime.now().astimezone().isoformat(), 'products': items,
        'policy': 'Public Huawei consumer product pages only. No live sell price claims. Variant conditions and original source snapshots retained.'}, ensure_ascii=False, indent=2), encoding='utf-8')
    print('Saved '+str(len(items))+' records to '+str(output), flush=True)


if __name__ == '__main__':
    main()
