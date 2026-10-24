"""Capture public VMALL product SSR, SKU-bound media and resumable content hashes.

Uses the navigation/search discovery manifest, never guesses product IDs. Product
reviews, customer photos and recommendation feeds are not part of this archive.
Run metadata locally or a complete media archive on an owned storage server.
"""
from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
import hashlib
import io
import json
from pathlib import Path
import shutil
import sys
import threading
import time
from urllib.parse import urlsplit, urljoin

import httpx
from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from app.vmall_catalog import parse_product_page, safe_asset_url, utc_now


def write_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix+'.tmp')
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding='utf-8')
    temporary.replace(path)


class Pace:
    def __init__(self, interval):
        self.interval = interval; self.next = 0; self.lock = threading.Lock()

    def wait(self):
        with self.lock:
            now = time.monotonic(); delay = max(0, self.next-now)
            self.next = now+delay+self.interval
        if delay: time.sleep(delay)

    def backoff(self, seconds):
        with self.lock: self.next=max(self.next,time.monotonic()+seconds)


class Capture:
    def __init__(self, output, *, min_free_gb=5, asset_interval=.12):
        self.output = output; self.min_free_bytes = min_free_gb*1024**3
        self.detail_pace = Pace(.45); self.asset_pace = Pace(max(.05,asset_interval))
        self.client = httpx.Client(timeout=45, follow_redirects=False,
            headers={'User-Agent': 'MateQuill/1.0 public product material research'})
        self.output.mkdir(parents=True, exist_ok=True)

    def space(self):
        if shutil.disk_usage(self.output).free < self.min_free_bytes:
            raise RuntimeError('storage_reserve_reached')

    def product(self, listing):
        identifier = str(listing.get('productId') or listing.get('prdId') or '')
        if not identifier.isdecimal():
            return {'spu_id': identifier, 'status': 'failed', 'error': 'invalid_public_product_id'}
        path = self.output/'products'/(identifier+'.json')
        if path.is_file():
            result = json.loads(path.read_text(encoding='utf-8'))
            if result.get('detail_verified'): return result
        unavailable=self.output/'unavailable'/(identifier+'.json')
        if unavailable.is_file():return json.loads(unavailable.read_text(encoding='utf-8'))
        url = 'https://www.vmall.com/product/'+identifier+'.html'
        try:
            self.space(); self.detail_pace.wait()
            target = url
            for _ in range(5):
                response = self.client.get(target)
                if not response.is_redirect: break
                if not response.headers.get('location'):
                    # Next SSR can explicitly return noData with 307 and no
                    # redirect target for stale navigation/search entries.
                    import re
                    match=re.search(r'<script\b[^>]*id=["\']__NEXT_DATA__["\'][^>]*>(.*?)</script>',response.text,re.S)
                    props=json.loads(match[1]).get('props',{}).get('pageProps',{}) if match else {}
                    if props.get('errorCode')=='noData':
                        result={'spu_id':identifier,'status':'public_detail_unavailable',
                            'source_url':url,'captured_at':utc_now(),'http_status':response.status_code,
                            'source_sha256':hashlib.sha256(response.content).hexdigest(),'error_code':'noData',
                            'scope':'Public detail returned noData; not a claim about all sales channels.'}
                        write_json(self.output/'unavailable'/(identifier+'.json'),result)
                        return result
                    raise ValueError('product_redirect_without_target')
                target = urljoin(target,response.headers.get('location',''))
                parsed = urlsplit(target)
                if (parsed.scheme!='https' or parsed.hostname not in {'www.vmall.com','item.vmall.com'}
                        or parsed.username or parsed.password or parsed.port not in (None,443)):
                    raise ValueError('unexpected_product_redirect')
            response.raise_for_status()
            if response.is_redirect or len(response.content)>12*1024**2:
                raise ValueError('unexpected_product_response')
            result = parse_product_page(response.content, listing, source_url=url)
            write_json(path, result)
            return result
        except Exception as exc:
            result = {'spu_id': identifier, 'status': 'failed', 'source_url': url,
                      'error': type(exc).__name__+':'+str(exc)[:180], 'captured_at': utc_now()}
            write_json(self.output/'failures'/(identifier+'.json'), result)
            return result

    def asset(self, row):
        url = row['image_url']; key = hashlib.sha256(url.encode()).hexdigest()
        sidecar = self.output/'downloads'/(key+'.json')
        if sidecar.exists():
            cached = json.loads(sidecar.read_text(encoding='utf-8'))
            path = self.output/cached.get('path', '__missing__')
            if (cached.get('download_status') == 'downloaded' and path.is_file()
                    and path.stat().st_size == cached.get('bytes')):
                with path.open('rb') as stream:
                    if hashlib.file_digest(stream, 'sha256').hexdigest() == cached.get('sha256'):
                        return cached
        if not safe_asset_url(url):
            return {'image_url': url, 'download_status': 'host_not_enabled'}
        try:
            self.space(); self.asset_pace.wait()
            limit = 512*1024**2 if row.get('media_type') == 'video' else 48*1024**2
            temporary = self.output/'objects'/(key+'.part'); temporary.parent.mkdir(parents=True, exist_ok=True)
            digest = hashlib.sha256(); count = 0
            with self.client.stream('GET', url) as response:
                if response.status_code==429:
                    try: delay=float(response.headers.get('retry-after','30'))
                    except ValueError: delay=30
                    self.asset_pace.backoff(max(5,min(delay,120)))
                response.raise_for_status()
                if response.is_redirect: raise ValueError('asset_redirect_requires_source_review')
                content_type = response.headers.get('content-type', '').split(';')[0]
                with temporary.open('wb') as stream:
                    for block in response.iter_bytes(128*1024):
                        count += len(block)
                        if count>limit: raise ValueError('asset_size_limit')
                        self.space(); digest.update(block); stream.write(block)
            if not count: raise ValueError('empty_asset')
            sha = digest.hexdigest(); dimensions = {}
            if row.get('media_type') == 'video':
                with temporary.open('rb') as probe: signature = probe.read(8)
                if not (content_type.startswith('video/') or signature.endswith(b'ftyp')):
                    raise ValueError('unexpected_video_type')
                suffix = '.mp4'
            else:
                with Image.open(temporary) as picture:
                    dimensions={'width':picture.width, 'height':picture.height, 'format':picture.format}
                    suffix = '.'+{'JPEG':'jpg','PNG':'png','WEBP':'webp','GIF':'gif','AVIF':'avif'}.get(picture.format,'img')
                    # Archive long-detail source bytes without loading the full
                    # pixel plane; rendering keeps its separate image bound.
                    large=picture.width*picture.height>80_000_000
                    dimensions.update(render_eligible=not large,
                        archive_validation='header_and_source_bytes' if large else 'image_structure_and_source_bytes')
                    if not large:picture.verify()
            final = self.output/'objects'/(sha+suffix)
            if final.exists(): temporary.unlink()
            else: temporary.replace(final)
            result = {'image_url': url, 'download_status': 'downloaded', 'sha256': sha,
                      'bytes': count, 'path': final.relative_to(self.output).as_posix(),
                      'content_type': content_type, 'downloaded_at': utc_now(), **dimensions}
        except Exception as exc:
            temporary = self.output/'objects'/(key+'.part')
            if temporary.exists(): temporary.unlink()
            result={'image_url':url,'download_status':'failed','error':type(exc).__name__+':'+str(exc)[:160]}
        write_json(sidecar,result)
        return result


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--discovery',type=Path,required=True)
    parser.add_argument('--output',type=Path,required=True)
    parser.add_argument('--workers',type=int,default=3)
    parser.add_argument('--asset-workers',type=int,default=4)
    parser.add_argument('--asset-interval',type=float,default=.12,help='Global media request interval, minimum .05s; 429 backs off')
    parser.add_argument('--download',action='store_true')
    parser.add_argument('--min-free-gb',type=float,default=5)
    parser.add_argument('--limit',type=int,default=0,help='Explicit smoke-test bound, never a complete capture')
    args=parser.parse_args()
    discovery=json.loads(args.discovery.read_text(encoding='utf-8'))
    merged={}
    for row in [*discovery['products'], *discovery.get('navigation_products', [])]:
        identifier=str(row.get('productId') or row.get('prdId') or '')
        if identifier and identifier not in merged: merged[identifier]=row
    listings=list(merged.values()); original_count=len(listings)
    if args.limit: listings=listings[:args.limit]
    capture=Capture(args.output,min_free_gb=args.min_free_gb,asset_interval=args.asset_interval)
    report={'started_at':utc_now(),'status':'running','discovery_coverage':discovery.get('coverage'),
            'discovered_products':original_count,'requested_products':len(listings),'limit':args.limit,
            'scope':'public_navigation_and_search_products_not_certified_entire_global_index',
            'products_completed':0,'products_failed':0,'products_unavailable':0,'asset_urls':0,'assets_downloaded':0,'assets_failed':0}
    write_json(args.output/'report.json',report)
    products=[]; failures=[]
    with ThreadPoolExecutor(max_workers=max(1,min(args.workers,4))) as pool:
        for future in as_completed([pool.submit(capture.product,row) for row in listings]):
            row=future.result()
            (products if row.get('detail_verified') else failures).append(row)
            report.update(products_completed=len(products),products_failed=sum(r.get('status')!='public_detail_unavailable' for r in failures),
                          products_unavailable=sum(r.get('status')=='public_detail_unavailable' for r in failures))
            write_json(args.output/'report.json',report)
            if (len(products)+len(failures))%20==0:print(json.dumps({k:v for k,v in report.items() if k!='discovery_coverage'},ensure_ascii=True),flush=True)
    media={}
    for product in products:
        for asset in product.get('assets',[]):media.setdefault(asset['image_url'],asset)
    report['asset_urls']=len(media); write_json(args.output/'report.json',report)
    downloads={}
    if args.download:
        with ThreadPoolExecutor(max_workers=max(1,min(args.asset_workers,12))) as pool:
            for future in as_completed([pool.submit(capture.asset,row) for row in media.values()]):
                result=future.result();downloads[result['image_url']]=result
                key='assets_downloaded' if result.get('download_status')=='downloaded' else 'assets_failed'
                report[key]+=1
                if len(downloads)%100==0:
                    write_json(args.output/'report.json',report)
                    print(json.dumps({k:v for k,v in report.items() if k!='discovery_coverage'},ensure_ascii=True),flush=True)
    assets=[]
    for product in products:
        for asset in product.get('assets',[]):
            asset.update(downloads.get(asset['image_url'],{}));assets.append(asset)
    # Thin, review-free catalog for applications; full parameter/detail projections stay in products/.
    thin=[{k:v for k,v in row.items() if k not in {'detail_html','service_conditions','listing'}} for row in products]
    write_json(args.output/'catalog.json',{'schema_version':1,'snapshot_at':discovery.get('snapshot_at'),
        'coverage':discovery.get('coverage'),'products':thin})
    write_json(args.output/'assets.json',{'schema_version':1,'snapshot_at':discovery.get('snapshot_at'),'assets':assets})
    report.update(status='complete' if not report['products_failed'] and not report['assets_failed'] else 'partial',
                  finished_at=utc_now(),catalog_complete_within_discovery=not report['products_failed'] and not args.limit,
                  media_complete=bool(args.download) and not report['assets_failed'] and not report['products_failed'] and not args.limit,
                  global_all_products_proven=False)
    write_json(args.output/'report.json',report);print(json.dumps({k:v for k,v in report.items() if k!='discovery_coverage'},ensure_ascii=True))


if __name__=='__main__':main()
