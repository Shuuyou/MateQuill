"""Collect bounded, product-scoped first-party promotional images.

This script deliberately ignores global navigation/recommendation pictures and
keeps pixel review separate from source metadata. No browser bypass is used.
"""
from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
import hashlib
from html import unescape
from html.parser import HTMLParser
from io import BytesIO
import json
from pathlib import Path
import re
from urllib.parse import urljoin, urlparse

import httpx
from PIL import Image, ImageDraw

ROOT = Path(__file__).resolve().parents[1]
OUTPUT = ROOT / 'runtime' / 'official-assets'
# Explicit aliases verified in the corresponding official page HTML. Do not
# infer arbitrary newer-version directories. Fold's 2026 resource directory is
# permitted after matching the ZIP's X90 Plus/HarmonyOS 6.1/full specification.
RESOURCE_ALIASES = {
    'matebook-d-16-2024': ('matebook-d-16-2024-new',),
    'matebook-d-16-se-2024': ('matebook-d-16-2024',),
    'matebook-fold-ultimate-design': ('matebook-fold-ultimate-design-2026',),
}


class ProductImages(HTMLParser):
    def __init__(self, page: str, slug: str):
        super().__init__()
        self.page, self.slug, self.items = page, slug, []

    def handle_starttag(self, tag, attrs):
        if tag != 'img':
            return
        attrs = dict(attrs)
        source = attrs.get('data-src') or attrs.get('src', '')
        parsed = urlparse(urljoin(self.page, source))
        # The exact product's own DAM directory is essential: product pages also
        # contain site-wide navigation and recommended unrelated products.
        resource_slugs = (self.slug,) + RESOURCE_ALIASES.get(self.slug, ())
        if parsed.hostname != 'consumer.huawei.com' or not any(f'/{slug}/' in parsed.path for slug in resource_slugs):
            return
        if not re.search(r'\.(jpg|jpeg|png|webp)$', parsed.path, re.I):
            return
        filename = parsed.path.rsplit('/', 1)[-1].lower()
        if re.search(r'thumb|icon|logo|pointer|mask|line-\d|harmonyos\.png|text|button|lock-screen-paper|computer-frame', filename):
            return
        alt = attrs.get('alt', '').strip()
        score = 10 if alt else 0
        for keyword in ('ux', 'meeting', 'research', 'pairing', 'collaboration', 'super-device',
                        'eyecare', 'battery-life', 'batterylife', 'office', 'learning', 'port', 'interface',
                        'claw', 'ai-hardware', 'touchpad'):
            if keyword in filename:
                score += 8
        for keyword in ('scene', 'handwriting', 'application', 'wlan-bridge', 'notes'):
            if keyword in filename:
                score += 20
        if 'portable-keyboard' in filename or 'huawei-m-pen-' in filename:
            score -= 25
        if 'kv' in filename:
            score += 5
        if 'highlights' in filename or 'color-' in filename or re.search(r'-(purple|yellow|silver|grey|gray|white|black|pink|green)-\d', filename):
            score -= 12
        if filename.endswith('.png') and not alt:
            score -= 15
        role = 'campaign_key_visual' if re.search(r'/hero/kv\.(?:jpg|jpeg|png|webp)$', parsed.path, re.I) else 'product'
        self.items.append({'image_url': parsed.geturl(), 'alt': alt, 'score': score, 'visual_role': role})


def prioritized_candidates(items):
    """Reserve the first successful slot for an exact product hero KV.

    Alt-labelled product angles can score above a page's complete launch
    composition. A semantic reservation keeps that visual available at any
    positive collection limit without guessing resource URLs.
    """
    return sorted(items, key=lambda item: (
        0 if item.get('visual_role') == 'campaign_key_visual' else
        1 if item.get('asset_kind') == 'packshot' else 2, -item['score']))


def comparison_subject_candidates(html, page, product):
    """Read a labeled comparison column, never infer SKU from a DAM filename."""
    from bs4 import BeautifulSoup
    soup = BeautifulSoup(html, 'html.parser')
    normalize = lambda text: re.sub(r'\s+', '', text).casefold()
    found = []
    for row in soup.select('.accessory-compare-row'):
        labels = row.select('.accessory-compare-header-product-name')
        groups = row.select('.accessory-compare-product-switch-item')
        if not labels or not groups or normalize(labels[0].get_text()) != normalize(product):
            continue
        group = groups[0]
        urls = [node.get('data-srcset', '').split()[0] for node in group.select('source[type="image/png"]')
                if node.get('data-srcset')]
        urls += [node.get('data-src') or node.get('src', '') for node in group.select('img')]
        for value in dict.fromkeys(urls):
            parsed = urlparse(urljoin(page, value))
            if parsed.hostname != 'consumer.huawei.com' or not parsed.path.lower().endswith('.png'):
                continue
            found.append({'image_url': parsed.geturl(), 'alt': product + ' / official comparison product image',
                'score': 100, 'visual_role': 'product', 'asset_kind': 'packshot',
                'source_binding': {'kind': 'first_labeled_comparison_column', 'product': labels[0].get_text(' ', strip=True)}})
            break
    return found


def sources():
    roots = list((ROOT / 'research' / 'knowledge').glob('*/P1-产品参数库'))
    if not roots:
        raise SystemExit('Local P1 product facts not found')
    for fact in sorted(roots[0].rglob('产品事实.md')):
        text = fact.read_text(encoding='utf-8-sig')
        title = text.splitlines()[0].removeprefix('# ').removesuffix(' - 产品事实')
        page = re.search(r'产品页：(https://\S+)', text)
        if page:
            yield title, page.group(1)


def collect_product(source, limit):
    product, page = source
    slug = urlparse(page).path.strip('/').split('/')[-1]
    dest = OUTPUT / slug
    dest.mkdir(parents=True, exist_ok=True)
    result, errors, seen = [], [], set()
    with httpx.Client(timeout=35, follow_redirects=True,
                      headers={'User-Agent': 'HuaweiHackathonResearch/1.0'}) as client:
        try:
            response = client.get(page)
            response.raise_for_status()
            if response.url.host != 'consumer.huawei.com':
                raise ValueError('Unexpected redirect host')
            # httpx may guess ISO-8859-1 when the site omits a charset header.
            html = response.content.decode('utf-8', errors='replace')
            parser = ProductImages(page, slug)
            parser.feed(html)
            parser.items.extend(comparison_subject_candidates(html, page, product))
        except Exception as exc:
            return [], [{'product': product, 'source_url': page, 'error': str(exc)}]
        for candidate in prioritized_candidates(parser.items):
            url = candidate['image_url']
            if url in seen or len(result) >= limit:
                continue
            seen.add(url)
            try:
                response = client.get(url)
                response.raise_for_status()
                if response.url.host != 'consumer.huawei.com':
                    raise ValueError('Unexpected image redirect host')
                raw = response.content
                if len(raw) > 15_000_000:
                    raise ValueError('Image exceeds bounded download size')
                image = Image.open(BytesIO(raw))
                width, height = image.size
                if width < 500 or height < 240:
                    continue
                digest = hashlib.sha256(raw).hexdigest()
                if any(item['sha256'] == digest for item in result):
                    continue
                suffix = {'JPEG': '.jpg', 'PNG': '.png', 'WEBP': '.webp'}.get(image.format)
                if not suffix:
                    continue
                target = dest / (digest[:16] + suffix)
                target.write_bytes(raw)
                alt = candidate['alt']
                result.append({'product': product, 'path': target.relative_to(ROOT).as_posix(),
                               'source_url': page, 'image_url': url, 'title': alt or url.rsplit('/', 1)[-1],
                               'alt': alt, 'context': f'华为官方产品页面：{product}；图片说明为网站 alt，不是像素核验结果。',
                               'sha256': digest, 'captured_at': datetime.now(timezone.utc).isoformat(),
                               'mime': Image.MIME.get(image.format, 'application/octet-stream'),
                               'width': width, 'height': height, 'visual_review_status': 'unreviewed',
                               'visual_role': candidate.get('visual_role', 'product'),
                               'asset_kind': candidate.get('asset_kind', 'unknown'),
                               'source_binding': candidate.get('source_binding', {}),
                               'scope_note': ('已核对 ZIP 与官网均为麒麟 X90 Plus / HarmonyOS 6.1、18/13 英寸 Fold 配置；页面部分小艺任务功能需升级 HarmonyOS 7，图片不证明默认系统支持。'
                                              if slug == 'matebook-fold-ultimate-design' else
                                              '关联精确产品页；配置与功能仍须核对对应销售版本，不证明所有 SKU 均支持。')})
            except Exception as exc:
                errors.append({'product': product, 'image_url': url, 'error': str(exc)})
    return result, errors


def contact_sheets(assets):
    products = list(dict.fromkeys(item['product'] for item in assets))
    for index, product in enumerate(products, 1):
        selected = [item for item in assets if item['product'] == product]
        sheet = Image.new('RGB', (1000, ((len(selected) + 3) // 4) * 190), 'white')
        draw = ImageDraw.Draw(sheet)
        for n, asset in enumerate(selected):
            image = Image.open(ROOT / asset['path']).convert('RGB')
            image.thumbnail((240, 155))
            x, y = (n % 4) * 250, (n // 4) * 190
            sheet.paste(image, (x, y))
            draw.text((x, y + 157), f"{n + 1}: {asset['sha256'][:12]}", fill='black')
        sheet.save(OUTPUT / f'contact-{index:02}.jpg', quality=90)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--per-product', type=int, default=10)
    parser.add_argument('--product-contains', default='', help='Append selected products to an existing manifest')
    args = parser.parse_args()
    if not 1 <= args.per_product <= 12:
        parser.error('per-product must be between 1 and 12')
    OUTPUT.mkdir(parents=True, exist_ok=True)
    selected = [source for source in sources() if args.product_contains in source[0]]
    with ThreadPoolExecutor(max_workers=3) as executor:
        batches = list(executor.map(lambda source: collect_product(source, args.per_product), selected))
    assets = [asset for batch, _ in batches for asset in batch]
    errors = [error for _, batch in batches for error in batch]
    old_path = ROOT / 'data' / 'official_assets.json'
    if args.product_contains and old_path.exists():
        old = json.loads(old_path.read_text(encoding='utf-8'))
        replaced = {product for product, _ in selected}
        assets = [a for a in old.get('assets', []) if a['product'] not in replaced] + assets
        errors = old.get('errors', []) + errors
    metadata = {'schema_version': 1, 'captured_at': datetime.now(timezone.utc).isoformat(),
                'provenance': 'First-party product-scoped consumer.huawei.com DAM images; pixel review pending.',
                'assets': assets, 'errors': errors,
                'sources': [{'product': product, 'source_url': page,
                             'asset_count': sum(a['product'] == product for a in assets),
                             'note': ('ZIP full specification matches current X90 Plus / HarmonyOS 6.1 official specification. 2026 DAM directory explicitly allowed; HarmonyOS 7-only marketing features require upgrade.'
                                      if 'matebook-fold-ultimate-design/' in page else
                                      'Exact official page source; SKU feature/compatibility claims require separate evidence.')}
                            for product, page in sources()]}
    (ROOT / 'data').mkdir(exist_ok=True)
    (ROOT / 'data' / 'official_assets.json').write_text(json.dumps(metadata, ensure_ascii=False, indent=2), encoding='utf-8')
    contact_sheets(assets)
    products = list(dict.fromkeys(a['product'] for a in assets))
    (OUTPUT / 'contact-index.json').write_text(json.dumps(
        {f'contact-{i:02}.jpg': product for i, product in enumerate(products, 1)},
        ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps({'assets': len(assets), 'products': len(set(a['product'] for a in assets)), 'errors': len(errors)}))


if __name__ == '__main__':
    main()
