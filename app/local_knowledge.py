"""Authorized archive + collected official sources, independent of ima quota."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from urllib.parse import urlparse

from .config import ROOT
from .demo import DemoProvider
from .models import Asset, EvidenceBundle, Fact, Source
from .facts import rank_assets_for_scene
from .visual_assets import review_asset_bytes
from .workflow import norm


def official_url(value):
    try:
        parsed = urlparse(str(value))
        return parsed.scheme == 'https' and not parsed.username and not parsed.password and (
            parsed.hostname in ('consumer.huawei.com', 'www.vmall.com', 'vmall.com', 'item.vmall.com', 'item.m.vmall.com'))
    except ValueError:
        return False


class LocalKnowledgeProvider(DemoProvider):
    def __init__(self, root=None, official_products=None, official_assets=None):
        super().__init__(root)
        self.products_file = Path(official_products) if official_products else ROOT/'data/official_products.json'
        self.assets_file = Path(official_assets) if official_assets else ROOT/'data/official_assets.json'
        self._asset_digests = {}

    async def get_asset(self, asset_id):
        path = self._assets.get(asset_id)
        expected = self._asset_digests.get(asset_id)
        if path and expected and path.is_file() and hashlib.sha256(path.read_bytes()).hexdigest()==expected:
            return path
        # Persisted creations outlive the process-local retrieval cache. Restore
        # only a public, hash-pinned official asset, never a caller-supplied path.
        for row in self._records(self.assets_file, 'assets'):
            digest = row.get('sha256', '')
            if asset_id != 'official-' + digest[:20] or not official_url(row.get('source_url', '')):
                continue
            target = (ROOT / str(row.get('path') or row.get('local_path') or '')).resolve()
            if not target.is_relative_to((ROOT / 'runtime/official-assets').resolve()) or not target.is_file():
                continue
            if target.suffix.lower() not in ('.png', '.jpg', '.jpeg', '.webp'):
                continue
            if hashlib.sha256(target.read_bytes()).hexdigest() != digest:
                continue
            self._assets[asset_id] = target
            self._asset_digests[asset_id] = digest
            return target
        return None

    @staticmethod
    def _records(path, key):
        try:
            data = json.loads(path.read_text(encoding='utf-8-sig'))
            rows = data.get(key, []) if isinstance(data, dict) else data
            return [row for row in rows if isinstance(row, dict)] if isinstance(rows, list) else []
        except (OSError, ValueError):
            return []

    def _product_records(self):
        if self.products_file == ROOT/'data/official_products.json':
            from .product_knowledge import load_products
            return load_products()
        return self._records(self.products_file, 'products')

    async def catalog(self):
        rows = await super().catalog()
        for item in rows:
            item['catalog_source'] = 'local_archive'
        names = {norm(x['name']) for x in rows}
        for row in self._product_records():
            name = row.get('product') or row.get('name', '')
            if not name or norm(name) in names or not row.get('facts'):
                continue
            url = row.get('spec_url') or row.get('product_url')
            if not official_url(url) and not (row.get('reviewed') and row.get('provider') == 'store_knowledge'):
                continue
            rows.append({'id':'official-'+hashlib.sha256(name.encode()).hexdigest()[:12], 'name':name,
                         'system':row.get('system','待核对具体配置'), 'catalog_source':row.get('provider','huawei_official'),
                         'category':row.get('category','computer'), 'aliases':row.get('aliases',[])})
            names.add(norm(name))
        metadata = {norm(row.get('product') or row.get('name','')): row for row in self._product_records()}
        for item in rows:
            record = metadata.get(norm(item['name']), {})
            item['category'] = record.get('category', 'computer')
            item['aliases'] = record.get('aliases', [])
            # Archive directory labels describe folders, not the device OS.
            # Prefer the exact-model official specification when available.
            os_fact = next((f for f in record.get('facts', [])
                            if f.get('label') == '操作系统'), None)
            if os_fact:
                item['system'] = os_fact['value']
            elif record.get('system'):
                item['system'] = record['system']
        return rows

    async def status(self):
        return {'connected':True, 'mode':'local', 'products':len(await self.catalog()),
                'message':'本地附件与已采集官方资料；不依赖 ima 配额'}

    async def retrieve(self, product, query=''):
        archive = await super().retrieve(product, query)
        if archive.conflicts:
            return archive
        has_archive = bool(archive.facts)
        bundle = archive.model_copy(deep=True) if has_archive else EvidenceBundle(product=product)
        bundle.warnings = []
        for source in bundle.sources:
            source.provider = 'local_archive'
            source.title = product+' / 赛事知识库 ZIP · 产品事实'
            source.sha256 = hashlib.sha256(source.excerpt.encode('utf-8')).hexdigest()
        for asset in bundle.assets:
            asset.provenance = 'local_archive'
            # Byte integrity and archive association, NOT a visual/SKU certificate.
            asset.verified = True
            path = self._assets.get(asset.id)
            if path:
                asset.sha256 = hashlib.sha256(path.read_bytes()).hexdigest()
        if has_archive:
            bundle.warnings.append('使用赛事 ZIP 原文快照；价格、活动和软件适配的时效须单独核对，不因改用本地来源降低事实要求。')
        for row in self._product_records():
            name = row.get('product') or row.get('name', '')
            if norm(name) != norm(product):
                continue
            url = row.get('spec_url') or row.get('product_url') or ''
            reviewed = row.get('reviewed') and row.get('provider') == 'store_knowledge'
            if not official_url(url) and not reviewed:
                continue
            records = row.get('facts') or []
            # Each fact remains linked to its exact collected span, never inferred
            # from a page title or an unqualified list of recommendation cards.
            for record in records:
                if not isinstance(record, dict):
                    continue
                quote, value, label = (str(record.get(k, '')).strip() for k in ('quote','value','label'))
                if not value or not label or value not in quote or not quote:
                    continue
                fact_url = record.get('source_url') or url
                if not official_url(fact_url) and not (reviewed and fact_url == url):
                    continue
                digest = hashlib.sha256(quote.encode()).hexdigest()
                sid = 'official-'+hashlib.sha256((fact_url+quote).encode()).hexdigest()[:20]
                fid = 'FO'+hashlib.sha256((product+label+value+sid).encode()).hexdigest()[:14]
                if any(norm(f.label)==norm(label) and norm(f.value)==norm(value) for f in bundle.facts):
                    continue
                provider = 'store_knowledge' if reviewed else 'huawei_official'
                bundle.sources.append(Source(id=sid, title=product+(' / 门店确认资料' if reviewed else ' / 华为官方规格'), provider=provider,
                    url=fact_url, excerpt=quote, sha256=digest, captured_at=row.get('captured_at','')))
                bundle.facts.append(Fact(id=fid, product=product, label=label, value=value, quote=quote,
                    source_id=sid, qualifiers=record.get('qualifiers') or [], system=record.get('system','')))
            if records:
                bundle.warnings.append('补充官方网页快照；不同代际、销售配置及页面限定条件不能交叉拼成一个 SKU。')
        self._add_official_assets(bundle, product, query)
        for asset in bundle.assets:
            self._asset_digests[asset.id] = asset.sha256
        systems = {f.value for f in bundle.facts if f.label=='操作系统'}
        if len(systems)==1:
            bundle.system=next(iter(systems))
        elif len(systems)>1:
            bundle.conflicts.append('附件与官方规格的操作系统记录不一致，需要先确认销售版本，不能混用。')
        bundle.verified_local = bool(bundle.facts and bundle.sources)
        bundle.verified_live = False
        if not bundle.verified_local:
            bundle.warnings.append('该准确型号尚无可核对的本地原文或已采集官方规格，不能用发现链接代替事实。')
        return bundle

    def _add_official_assets(self, bundle, product, query):
        seen = {a.sha256 for a in bundle.assets if a.sha256}
        for row in self._records(self.assets_file,'assets'):
            if norm(row.get('product','')) != norm(product) or not official_url(row.get('source_url','')):
                continue
            value = row.get('path') or row.get('local_path')
            if not value:
                continue
            path = (ROOT/str(value)).resolve()
            media_root = (ROOT/'runtime/official-assets').resolve()
            if not path.is_relative_to(media_root) or not path.is_file() or path.suffix.lower() not in ('.png','.jpg','.jpeg','.webp'):
                continue
            raw = path.read_bytes()
            digest = hashlib.sha256(raw).hexdigest()
            if digest != row.get('sha256') or digest in seen:
                continue
            reviewed = review_asset_bytes(raw, row.get('title') or row.get('alt') or '官方产品页素材')
            if not reviewed.get('usable',True):
                continue
            sid = 'official-image-'+hashlib.sha256(row['source_url'].encode()).hexdigest()[:16]
            if not any(s.id==sid for s in bundle.sources):
                bundle.sources.append(Source(id=sid, title=product+' / 华为官方产品素材', provider='huawei_official',
                    url=row['source_url'], captured_at=row.get('captured_at','')))
            aid = 'official-'+digest[:20]
            meta = {k:reviewed[k] for k in ('title','original_title','semantic_tags','visual_role','semantic_basis','claim_scope','visual_group')}
            bundle.assets.append(Asset(id=aid, product=product, source_id=sid, url='/api/assets/'+aid,
                provenance='huawei_official', verified=True, sha256=digest, captured_at=row.get('captured_at',''), **meta))
            self._assets[aid] = path
            seen.add(digest)
        bundle.assets = rank_assets_for_scene(bundle.assets,query)[:12]
