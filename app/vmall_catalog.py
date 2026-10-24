"""SKU-bound public VMALL snapshots. Discovery is separate from writing evidence."""
from __future__ import annotations

from datetime import datetime, timezone
import hashlib
from html import unescape
from html.parser import HTMLParser
import json
from pathlib import Path
import re
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parents[1]
ASSET_HOSTS = frozenset({'res.vmallres.com', 'res1.vmallres.com', 'res2.vmallres.com',
                         'fss-cdn.vmall.com', 'consumer.huawei.com', 'vod.vmall.com'})


def utc_now():
    return datetime.now(timezone.utc).isoformat()


def safe_asset_url(value):
    if not isinstance(value, str):
        return None
    value = unescape(value.strip())
    if value.startswith('//'):
        value = 'https:' + value
    try:
        p = urlsplit(value)
        if (p.scheme == 'https' and p.hostname in ASSET_HOSTS and not p.username
                and not p.password and p.port in (None, 443)):
            return value
    except ValueError:
        pass
    return None


def valid_vmall_source(source):
    value = source.get('url', '') if isinstance(source, dict) else getattr(source, 'url', '')
    digest = source.get('sha256', '') if isinstance(source, dict) else getattr(source, 'sha256', '')
    try:
        p = urlsplit(value)
        return bool(p.scheme == 'https' and p.hostname in {'www.vmall.com','vmall.com','item.vmall.com'}
                    and not p.username and not p.password and p.port in (None,443)
                    and re.fullmatch(r'/product/[0-9]+\.html',p.path)
                    and re.fullmatch(r'[a-f0-9]{64}',digest))
    except (ValueError,TypeError):
        return False


class _Media(HTMLParser):
    def __init__(self):
        super().__init__(); self.rows = []

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        if tag not in {'img', 'video', 'source'}:
            return
        candidates = [(key, a.get(key, '')) for key in ('src', 'data-src', 'poster')]
        candidates += [('srcset', part.strip().split(' ')[0]) for part in a.get('srcset', '').split(',') if part.strip()]
        for key, url in candidates:
            url = unescape(url)
            if url.startswith('//'): url = 'https:' + url
            if url.startswith('https://'):
                is_video = (tag == 'video' or tag == 'source' and not a.get('type', '').startswith('image/')) and key != 'poster'
                self.rows.append({'url': url, 'media_type': 'video' if is_video else 'image', 'alt': a.get('alt', '')[:500]})


def _photo(row):
    if not isinstance(row, dict):
        return None
    if row.get('imgUri'):
        return safe_asset_url(row['imgUri'])
    path, name = row.get('photoPath'), row.get('photoName')
    if isinstance(path, str) and isinstance(name, str) and path.startswith('/') and '/' not in name:
        # The published gallery delivery format, not a guessed product/resource ID.
        return safe_asset_url('https://res.vmallres.com/pimages' + path + '800_800_' + name)
    return None


def parse_product_page(raw, listing, *, source_url, captured_at=None):
    """Project first-party SSR fields; never ingest reviews, recommendations or avatars."""
    html = raw.decode('utf-8', errors='replace') if isinstance(raw, bytes) else raw
    match = re.search(r'<script\b[^>]*\bid=["\']__NEXT_DATA__["\'][^>]*>(.*?)</script>', html, re.S)
    if not match:
        raise ValueError('vmall_product_ssr_missing')
    props = json.loads(match[1])['props']['pageProps']
    product_id = str(listing.get('productId') or listing.get('prdId') or '')
    current = props.get('mainData', {}).get('current') or {}
    actual_id = str(current.get('disPrdId') or props.get('prdId') or '')
    if not product_id or product_id != actual_id or not current.get('name'):
        raise ValueError('vmall_product_identity_mismatch')
    stamp = captured_at or utc_now()
    base = current.get('base') or {}
    if not isinstance(base, dict):
        raise ValueError('vmall_sku_map_missing')
    assets, variants, seen = [], [], set()

    def add(url, scope, sku=None, media_type='image', alt=''):
        if not url:
            return
        # Record outside-host references without downloading arbitrary hosts.
        if not isinstance(url, str) or not url.startswith(('https://', '//')):
            return
        if url.startswith('//'): url = 'https:' + url
        key = (url, scope, sku)
        if key in seen: return
        seen.add(key)
        assets.append({'id': 'vmall-asset-' + hashlib.sha256(repr(key).encode()).hexdigest()[:24],
                       'spu_id': product_id, 'sku_id': sku, 'scope': scope, 'image_url': url,
                       'media_type': media_type, 'alt': alt, 'source_url': source_url,
                       'captured_at': stamp, 'download_status': 'pending' if safe_asset_url(url) else 'host_not_enabled',
                       'provenance': 'public_official_store_material', 'event_authentication': False})

    for code, item in base.items():
        if not isinstance(item, dict): continue
        sku = str(item.get('sbomCode') or code)
        variants.append({'sku_id': sku, 'name': item.get('name', ''),
                         'attributes': item.get('gbomAttrList') or [],
                         'observed_price': item.get('price'), 'price_scope': 'vmall_snapshot_not_store_quote',
                         'raw_status': item.get('status'), 'button_mode': item.get('buttonMode'),
                         'stock_state': 'unknown', 'carrier_code': item.get('carrierCode'),
                         'source_gallery_descriptors': [{k:item.get(k) for k in ('photoPath','photoName','photoMaxSize')},
                                                        *(item.get('groupPhotoList') or [])]})
        add(_photo(item), 'sku_gallery', sku)
        for photo in item.get('groupPhotoList') or []:
            add(_photo(photo), 'sku_gallery', sku)
    for photo in props.get('mainData', {}).get('galleryData') or []:
        add(_photo(photo), 'selected_sku_gallery', str(current.get('currentSbomCode') or props.get('sbomCode') or ''))
    ext = props.get('extData') or {}
    detail = ext.get('detailsData') or {}
    detail_html = detail.get('data') if isinstance(detail, dict) else ''
    parser = _Media(); parser.feed(detail_html if isinstance(detail_html, str) else '')
    for media in parser.rows:
        add(media['url'], 'product_detail', media_type=media['media_type'], alt=media['alt'])
    display = current.get('displayPrdVo') or {}
    for field in ('webVideoPath', 'mobileVideoPath'):
        add(display.get(field), 'product_video', media_type='video')
    for field in ('webVideoGroundPath', 'mobileVideoGroundPath'):
        add(display.get(field), 'product_video_poster')
    parameters = ext.get('parameterData') or {}
    sections = (parameters.get('list') or {}).get('data') or []
    shop = current.get('shopInfo') or {}
    inv = listing.get('isInv')
    result = {'catalog_key': 'vmall-product-' + product_id, 'spu_id': product_id,
              'name': current['name'], 'product': current['name'], 'provider': 'vmall_catalog',
              'category': listing.get('category') or 'other', 'brand': current.get('brandName', ''),
              'seller': {'name': shop.get('shopName') or listing.get('shopName', ''),
                         'code': shop.get('shopCode') or listing.get('carrierCode'), 'raw_shop_type': shop.get('shopType')},
              'source_url': source_url, 'captured_at': stamp, 'snapshot_at': stamp,
              'source_sha256': hashlib.sha256(raw if isinstance(raw, bytes) else raw.encode()).hexdigest(),
              'detail_verified': True, 'status': 'collected', 'system': '', 'facts': [],
              'availability': {'catalog_listed': True, 'sale_state': 'listed',
                               'stock_state': 'in_stock' if inv == 1 else 'out_of_stock' if inv == 0 else 'unknown',
                               'stock_sku_id': str(listing.get('skuCode') or '') or None,
                               'observed_at': stamp, 'scope': 'public_search_snapshot_not_local_store_inventory'},
              'listing': listing, 'variants': variants, 'assets': assets,
              'parameter_sections': sections, 'parameter_sku_id': str(props.get('sbomCode') or ''),
              'detail_html': detail_html if isinstance(detail_html, str) else '',
              'service_conditions': parameters.get('warranty') or '',
              'discovered_tags': listing.get('discovered_tags', [])}
    return result


def load_vmall_products(path=None):
    path = Path(path) if path else ROOT/'data/vmall_catalog.json'
    try:
        document = json.loads(path.read_text(encoding='utf-8'))
    except (OSError, ValueError):
        return []
    if not isinstance(document, dict) or not isinstance(document.get('products'), list):
        return []
    rows = document.get('products', [])
    return [row for row in rows if isinstance(row, dict) and row.get('catalog_key') and row.get('name') and row.get('detail_verified') is True
            and isinstance(row.get('availability'), dict) and row['availability'].get('catalog_listed') is True
            and row['availability'].get('sale_state') == 'listed']


def vmall_summary(row):
    return {'id': row['catalog_key'], 'catalog_key': row['catalog_key'], 'name': row['name'],
            'category': row.get('category', 'other'), 'system': '', 'source_url': row.get('source_url', ''),
            'fact_count': len(row.get('facts', [])), 'updated_at': row.get('captured_at', ''),
            'snapshot_at': row.get('snapshot_at'), 'availability': row.get('availability'),
            'spu_id': row.get('spu_id'), 'sku_id': row.get('sku_id'), 'brand': row.get('brand'),
            'seller': row.get('seller'), 'variant_count': len(row.get('variants', [])),
            'asset_count': len(row.get('assets', [])), 'discovery_only': True, 'catalog_source': 'vmall'}
