"""Export completed product projections and available media from an active archive.

No network requests. Full per-product projections remain in the archive; thin
application manifests retain exact selected-SKU parameter sections and remarks.
"""
from __future__ import annotations
import argparse
import hashlib
import json
from pathlib import Path
import sys

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from app.vmall_catalog import utc_now


def export_snapshot(discovery,archive,destination):
    allowed={str(row.get('productId') or row.get('prdId')) for row in
             [*discovery.get('products',[]),*discovery.get('navigation_products',[])]}
    products=[];assets=[];urls={};downloaded=set();missing=[];unavailable=[]
    for identifier in sorted(allowed):
        path=archive/'products'/(identifier+'.json')
        if not path.exists():
            if (archive/'unavailable'/(identifier+'.json')).exists(): unavailable.append(identifier)
            else: missing.append(identifier)
            continue
        row=json.loads(path.read_text(encoding='utf-8'))
        if not row.get('detail_verified'):missing.append(identifier);continue
        if isinstance(row.get('availability'),dict):
            row['availability']['stock_sku_id']=str((row.get('listing') or {}).get('skuCode') or '') or None
        for asset in row.get('assets',[]):
            url=asset['image_url'];key=hashlib.sha256(url.encode()).hexdigest()
            sidecar=archive/'downloads'/(key+'.json')
            if sidecar.exists():
                media=json.loads(sidecar.read_text(encoding='utf-8'))
                # A path is relative to this archive, never to the source repo.
                asset.update(media)
            asset['archive_relative_path']=asset.get('path')
            if asset.get('download_status')=='downloaded':downloaded.add(url)
            urls[url]=True;assets.append(asset)
        products.append({k:v for k,v in row.items() if k not in {'detail_html','service_conditions','listing'}})
    destination.mkdir(parents=True,exist_ok=True)
    report={'exported_at':utc_now(),'discovered_products':len(allowed),'verified_products':len(products),
            'missing_product_ids':missing,'sku_count':sum(len(p.get('variants',[])) for p in products),
            'public_detail_unavailable_ids':unavailable,
            'asset_references':len(assets),'unique_asset_urls':len(urls),'downloaded_urls':len(downloaded),
            'product_coverage_complete':not missing,'all_discovered_ids_accounted_for':not missing,
            'global_all_products_proven':False,
            'source_scope':discovery.get('coverage',{}).get('scope')}
    docs={'catalog.json':{'schema_version':1,'snapshot_at':discovery.get('snapshot_at'),
                          'coverage':discovery.get('coverage'),'archive_progress':report,'products':products},
          'assets.json':{'schema_version':1,'snapshot_at':discovery.get('snapshot_at'),'assets':assets},
          'export-report.json':report}
    for name,body in docs.items():
        path=destination/name;temp=path.with_suffix('.tmp')
        temp.write_text(json.dumps(body,ensure_ascii=False,indent=2),encoding='utf-8');temp.replace(path)
    return report


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--discovery',type=Path,required=True)
    p.add_argument('--archive',type=Path,required=True);p.add_argument('--output',type=Path,required=True);args=p.parse_args()
    report=export_snapshot(json.loads(args.discovery.read_text(encoding='utf-8')),args.archive,args.output)
    print(json.dumps({k:v for k,v in report.items() if k!='missing_product_ids'},ensure_ascii=True))
