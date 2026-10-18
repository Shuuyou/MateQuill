"""Download hash-pinned Huawei product pictures for a clean public deployment."""
from __future__ import annotations
import argparse,hashlib,json,urllib.request
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from urllib.parse import urlsplit
ROOT=Path(__file__).resolve().parents[1]

def fetch(row):
    relative=Path(row['path']);target=(ROOT/relative).resolve()
    if not target.is_relative_to((ROOT/'runtime/official-assets').resolve()):return False
    url=row.get('image_url','');parsed=urlsplit(url)
    if parsed.scheme!='https' or parsed.hostname!='consumer.huawei.com':return False
    expected=row.get('sha256','')
    if target.is_file() and hashlib.sha256(target.read_bytes()).hexdigest()==expected:return True
    try:
        request=urllib.request.Request(url,headers={'User-Agent':'MateQuill/1.0 product-source-loader'})
        with urllib.request.urlopen(request,timeout=30) as response:
            final=urlsplit(response.url)
            if final.scheme!='https' or final.hostname!='consumer.huawei.com':return False
            raw=response.read(12*1024*1024+1)
        if len(raw)>12*1024*1024 or hashlib.sha256(raw).hexdigest()!=expected:return False
        target.parent.mkdir(parents=True,exist_ok=True);target.write_bytes(raw)
        return True
    except Exception:
        return False

def main():
    parser=argparse.ArgumentParser();parser.add_argument('--per-product',type=int,default=12);args=parser.parse_args()
    rows=json.loads((ROOT/'data/official_assets.json').read_text(encoding='utf-8-sig'))['assets']
    chosen=[];counts={}
    for row in rows:
        product=row.get('product','');counts.setdefault(product,0)
        if counts[product]<args.per_product:chosen.append(row);counts[product]+=1
    with ThreadPoolExecutor(max_workers=6) as pool:outcomes=list(pool.map(fetch,chosen))
    print(f'Official source pictures: {sum(outcomes)}/{len(outcomes)} verified')
    if chosen and not any(outcomes):raise SystemExit('No verified pictures were downloaded')
if __name__=='__main__':main()
