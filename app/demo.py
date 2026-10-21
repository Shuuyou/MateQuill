"""Private local evidence preview. Never masquerades as a live ima response."""
from __future__ import annotations
import csv
import hashlib
import re
from pathlib import Path
from .models import EvidenceBundle, Fact, Asset, Source
from .facts import extract_product_facts, asset_semantics, rank_assets_for_scene
from .visual_assets import review_asset_bytes
from .workflow import norm


class DemoProvider:
    def __init__(self, root=None):
        self.root=Path(root) if root else Path(__file__).resolve().parent.parent/'research'/'knowledge'
        self._assets={}
        self._archive_digests={}

    def _products(self):
        found=[]
        if self.root.exists():
            for file in self.root.rglob('产品事实.md'):
                if 'P1-产品参数库' not in str(file):
                    continue
                text=file.read_text(encoding='utf-8-sig')
                first=next((x for x in text.splitlines() if x.startswith('# ')), '')
                title=re.sub(r'\s*[-—]\s*产品事实.*$','',first.lstrip('# ').strip())
                if title:
                    found.append((title,file,text))
        # A six-image model makes the first preview usable across all required platforms.
        return sorted(found,key=lambda row:(row[0]!='HUAWEI MateBook Pro',row[0]))

    async def catalog(self):
        return [{'id':hashlib.sha256(str(p).encode()).hexdigest()[:12], 'name':name,
                 'system':'鸿蒙' if 'Harmony' in text or '鸿蒙' in text else 'Windows/待确认'}
                for name,p,text in self._products()]

    async def status(self):
        return {'connected':False,'mode':'demo','message':'本地知识库预览，非ima实时连接','products':len(self._products())}

    async def probe(self):
        return await self.status()

    async def get_asset(self, asset_id):
        if not re.fullmatch(r'demo-[0-9a-f]{16}', asset_id):
            return None
        path=self._assets.get(asset_id)
        expected=self._archive_digests.get(asset_id)
        if path and expected:
            if self._safe_image(path) and hashlib.sha256(path.read_bytes()).hexdigest()==expected:
                return path
            # A known image changing in place must not be re-approved by a
            # fresh scan of the same index during this provider's lifetime.
            return None
        # IDs are deliberately unchanged: persisted creations use the original
        # lexical path hash, not a new hash of the resolved file or its bytes.
        if self.root.exists():
            for index in self.root.rglob('图片索引.csv'):
                if not index.resolve().is_relative_to(self.root.resolve()) or not index.is_file():
                    continue
                with index.open(encoding='utf-8-sig',newline='') as stream:
                    for row in csv.DictReader(stream):
                        local=index.parent/'images'/row.get('文件名','')
                        if ('demo-'+hashlib.sha256(str(local).encode()).hexdigest()[:16])!=asset_id:
                            continue
                        if not self._safe_image(local):
                            return None
                        self._assets[asset_id]=local
                        self._archive_digests[asset_id]=hashlib.sha256(local.read_bytes()).hexdigest()
                        return local
        return None

    def _safe_image(self, path):
        return (path.suffix.lower() in ('.png','.jpg','.jpeg','.webp')
                and path.resolve().is_relative_to(self.root.resolve()) and path.is_file())

    async def retrieve(self, product, query=''):
        rows=self._products()
        exact=[x for x in rows if norm(x[0])==norm(product)]
        if not exact:
            candidates=[x for x in rows if norm(product) in norm(x[0]) or norm(x[0]) in norm(product)]
            if len(candidates)==1:
                exact=candidates
            elif candidates:
                return EvidenceBundle(product=product,conflicts=['型号存在歧义，请从目录选择准确型号：'+'；'.join(x[0] for x in candidates[:6])])
        if not exact:
            return EvidenceBundle(product=product,warnings=['未找到本地精确型号；不以虚构参数替代。请从产品目录选择或配置ima。'])
        title,path,text=exact[0]
        sid='local-'+hashlib.sha256(str(path).encode()).hexdigest()[:12]
        source=Source(id=sid,title=title+'（本地私有附件预览）',provider='local_demo',excerpt=text)
        facts=extract_product_facts(text,title,sid)
        assets=[]
        seen_visual_groups=set()
        index=path.parent/'图片索引.csv'
        if index.exists():
            with index.open(encoding='utf-8-sig',newline='') as stream:
                for row in csv.DictReader(stream):
                    if norm(row.get('产品型号',''))!=norm(title):
                        continue
                    local=path.parent/'images'/row.get('文件名','')
                    if not self._safe_image(local):
                        continue
                    aid='demo-'+hashlib.sha256(str(local).encode()).hexdigest()[:16]
                    self._assets[aid]=local
                    self._archive_digests[aid]=hashlib.sha256(local.read_bytes()).hexdigest()
                    caption=row.get('官网图片说明','产品素材')
                    reviewed=review_asset_bytes(local.read_bytes(),caption)
                    if not reviewed.get('usable',True):
                        continue
                    visual_group=reviewed.get('visual_group','')
                    if visual_group and visual_group in seen_visual_groups:
                        continue
                    if visual_group:
                        seen_visual_groups.add(visual_group)
                    metadata={key:reviewed[key] for key in (
                        'title','original_title','semantic_tags','visual_role','semantic_basis','claim_scope','visual_group') if key in reviewed}
                    assets.append(Asset(id=aid,product=title,url='/api/assets/'+aid,source_id=sid,
                        local_path=str(local),**metadata,
                        provenance='local_demo',verified=False))
        assets=rank_assets_for_scene(assets,query)
        assets=[a for a in assets if a.visual_role!='decorative'][:8]
        return EvidenceBundle(product=title,facts=facts,assets=assets,sources=[source],
            warnings=['本地附件仅供私有预览；事实与图片未经本次ima实时检索确认。'],verified_live=False)
