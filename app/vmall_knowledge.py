"""Exact-name, SKU-bound VMALL parameters and bounded source-image delivery.

Storefront state is not store inventory or a price authorization. Parameters
remain scoped to the actual SSR parameter SKU, and pictures never become
packshots or photographic-performance evidence merely by being downloaded.
"""
from __future__ import annotations

import asyncio
import hashlib
from html.parser import HTMLParser
from io import BytesIO
import ipaddress
import json
from pathlib import Path
import re
import socket
import unicodedata
from urllib.parse import urlsplit

import httpx
from PIL import Image

from .config import ROOT
from .models import Asset, EvidenceBundle, Fact, Source
from .vmall_catalog import load_vmall_products, safe_asset_url
from .visual_assets import review_asset_bytes

MAX_IMAGE_BYTES = 12*1024*1024
MAX_IMAGE_PIXELS = 24_000_000
MAX_FACTS = 160
MIMES = {'JPEG': ('image/jpeg', '.jpg'), 'PNG': ('image/png', '.png'), 'WEBP': ('image/webp', '.webp')}


def _name(value):
    return re.sub(r'\s+', '', unicodedata.normalize('NFKC', str(value or ''))).casefold()


class _Text(HTMLParser):
    def __init__(self): super().__init__(convert_charrefs=True);self.parts=[];self.ignored=0
    def handle_starttag(self, tag, attrs):
        if tag in {'script', 'style'}: self.ignored += 1
        if tag in {'br','p','div','li'}: self.parts.append('\n')
    def handle_endtag(self, tag):
        if tag in {'script','style'}: self.ignored=max(0,self.ignored-1)
        if tag in {'p','div','li'}: self.parts.append('\n')
    def handle_data(self, data):
        if not self.ignored:self.parts.append(data)


def _text(value):
    if not isinstance(value, str):return ''
    parser=_Text();parser.feed(value)
    return '\n'.join(re.sub(r'[ \t\r]+',' ',line).strip() for line in ''.join(parser.parts).split('\n') if line.strip())


def _identity(row):
    spu=str(row.get('spu_id') or '')
    if not re.fullmatch(r'[0-9]{1,32}',spu):return None
    try:
        url=urlsplit(row.get('source_url') or '')
        valid=(url.scheme=='https' and url.hostname in {'www.vmall.com','vmall.com','item.vmall.com','item.m.vmall.com'}
               and not url.username and not url.password and url.port in (None,443)
               and url.path=='/product/'+spu+'.html')
    except (ValueError,TypeError):return None
    if not valid or not re.fullmatch(r'[a-f0-9]{64}',str(row.get('source_sha256') or '')):return None
    return spu


def parameter_sku(row):
    """No first-variant, gallery order, price or model-name SKU inference."""
    sku=str(row.get('parameter_sku_id') or '')
    variants=row.get('variants') or []
    matches=[v for v in variants if isinstance(v,dict) and str(v.get('sku_id') or '')==sku]
    return sku if sku and len(matches)==1 else None


def parameter_rows(row):
    if not _identity(row) or not parameter_sku(row):return []
    output=[];seen=set()
    sections=row.get('parameter_sections') or []
    if not isinstance(sections,list):sections=[]
    for section in sections[:80]:
        if not isinstance(section,dict) or not isinstance(section.get('data'),list):continue
        for item in section['data'][:300]:
            if not isinstance(item,dict):continue
            label,value,note=(_text(item.get(key,'')) for key in ('attrName','attrValue','attrRemark'))
            if not label or not value or value in {'-', '--', '暂无', '未知'}:continue
            if len(label)>300 or len(value)>12000 or len(note)>12000:continue
            if re.search(r'价格|售价|补贴|库存|销量|优惠券',label):continue
            key=(label,value,note)
            if key in seen:continue
            seen.add(key);output.append({'label':label,'value':value,'remark':note,'section':_text(section.get('name','')),
                                         'origin':'parameter_section'})
            if len(output)>=MAX_FACTS:return output
    # Attributes belong to the explicitly selected parameter SKU. Never use an
    # adjacent color/capacity variant to fill gaps in this specification.
    sku=parameter_sku(row)
    selected=next(v for v in row['variants'] if str(v.get('sku_id') or '')==sku)
    attributes=selected.get('attributes') or []
    if isinstance(attributes,list):
        for item in attributes[:160]:
            if not isinstance(item,dict):continue
            label,value,note=(_text(item.get(key,'')) for key in ('attrName','attrValue','attrRemark'))
            if not label or not value or value in {'-','--','暂无','未知'}:continue
            if len(label)>300 or len(value)>12000 or len(note)>12000 or re.search(r'价格|售价|补贴|库存|销量|优惠券',label):continue
            existing=[p for p in output if _name(p['label'])==_name(label)]
            if existing:
                # The structured parameter section takes precedence. A
                # conflicting attribute is not silently promoted or merged.
                if any(p['value']!=value for p in existing):continue
                if note and all(note not in p['remark'] for p in existing):
                    existing[0]['remark']='\n'.join(part for part in [existing[0]['remark'],note] if part)
                continue
            output.append({'label':label,'value':value,'remark':note,'section':'当前所选规格属性',
                           'origin':'selected_variant_attribute'})
            if len(output)>=MAX_FACTS:return output
    if not output:
        name=_text(selected.get('name',''))
        if name and name!=sku and len(name)<=12000:
            output.append({'label':'具体配置 / 商品名称','value':name,'remark':'',
                           'section':'当前所选商品','origin':'selected_variant_name'})
    return output


def match_vmall_product(product, *, rows=None):
    rows=load_vmall_products() if rows is None else rows
    selected=[row for row in rows if isinstance(row,dict) and _name(row.get('name'))==_name(product)]
    return selected[0] if _name(product) and len(selected)==1 and _identity(selected[0]) else None


def catalog_entries(existing_names=(), *, rows=None):
    rows=load_vmall_products() if rows is None else rows
    counts={}
    for row in rows:
        key=_name(row.get('name')) if isinstance(row,dict) else ''
        counts[key]=counts.get(key,0)+1
    existing={_name(name) for name in existing_names}
    result=[]
    for row in rows:
        if not isinstance(row,dict):continue
        key=_name(row.get('name'))
        if not key or key in existing or counts[key]!=1:continue
        params=parameter_rows(row)
        if not params:continue
        system=next((p['value'] for p in params if p['label']=='操作系统'),'')
        result.append({'id':row['catalog_key'],'name':row['name'],'category':row.get('category','other'),
            'system':system,'catalog_source':'vmall_official_snapshot','aliases':[],
            'parameter_sku_id':parameter_sku(row),'spu_id':row['spu_id'],'fact_count':len(params)})
    return result


def _source_title(row, product, kind):
    parts=[product+' / VMALL商城'+kind]
    brand=_text(row.get('brand',''))
    seller=row.get('seller') if isinstance(row.get('seller'),dict) else {}
    name=_text(seller.get('name',''))
    if brand:parts.append('品牌：'+brand)
    if name:parts.append('商家：'+name)
    return ' · '.join(parts)


def parameter_evidence(row, *, product=None):
    product=product or row.get('name','');bundle=EvidenceBundle(product=product)
    params=parameter_rows(row)
    if not params:return bundle
    sku=parameter_sku(row)
    scope='商城参数所属SKU：'+sku
    variant=next(v for v in row['variants'] if str(v.get('sku_id') or '')==sku)
    configuration=_text(variant.get('name',''))
    configuration=('适用规格：'+configuration) if configuration and configuration!=sku else ''
    sid='vmall-spec-'+hashlib.sha256((row['source_url']+row['source_sha256']+sku).encode()).hexdigest()[:24]
    quotes=[]
    for item in params:
        quote=scope+('\n'+configuration if configuration else '')+'\n'+item['label']+'：'+item['value']+('\n'+item['remark'] if item['remark'] else '')
        qualifiers=([configuration] if configuration else [])+([item['remark']] if item['remark'] else [])
        fid='FV'+hashlib.sha256((sid+quote).encode()).hexdigest()[:22]
        bundle.facts.append(Fact(id=fid,product=product,label=item['label'],value=item['value'],quote=quote,
            source_id=sid,qualifiers=qualifiers,sku_scope='vmall:'+row['spu_id']+':'+sku))
        quotes.append(quote)
    bundle.sources.append(Source(id=sid,title=_source_title(row,product,'当前规格资料'),provider='vmall_storefront',
        url=row['source_url'],sha256=row['source_sha256'],captured_at=row.get('captured_at',''),excerpt='\n\n'.join(quotes)))
    systems={p['value'] for p in params if p['label']=='操作系统'}
    if len(systems)==1:bundle.system=next(iter(systems))
    elif len(systems)>1:bundle.conflicts.append('商城当前参数SKU的操作系统记录存在冲突。')
    bundle.verified_local=True
    return bundle


def _validated_image(raw, mime=''):
    if not isinstance(raw,bytes) or not 0<len(raw)<=MAX_IMAGE_BYTES:raise ValueError('image_size')
    with Image.open(BytesIO(raw)) as image:
        if image.format not in MIMES or image.width*image.height>MAX_IMAGE_PIXELS or getattr(image,'n_frames',1)!=1:
            raise ValueError('image_format_or_pixels')
        actual,suffix=MIMES[image.format]
        if mime and mime not in {actual,'image/jpg' if actual=='image/jpeg' else actual}:raise ValueError('image_mime')
        image.verify()
    return hashlib.sha256(raw).hexdigest(),suffix


async def download_asset(url):
    """Exact first-party host, public DNS pinned TCP/TLS, no redirects or cookies."""
    approved=safe_asset_url(url)
    if not approved or any(ord(c)<33 for c in url):raise ValueError('asset_url')
    parsed=httpx.URL(approved);host=parsed.host
    records=await asyncio.wait_for(asyncio.get_running_loop().getaddrinfo(host,443,type=socket.SOCK_STREAM),timeout=5)
    addresses=list(dict.fromkeys(record[4][0] for record in records))
    if not addresses or any(not ipaddress.ip_address(ip).is_global for ip in addresses):raise ValueError('private_asset_host')
    pinned=parsed.copy_with(host=addresses[0])
    async with httpx.AsyncClient(timeout=15,follow_redirects=False,trust_env=False) as client:
        async with client.stream('GET',pinned,headers={'Host':host,'Connection':'close','User-Agent':'MateQuill/1.0 official product material'},
                                 extensions={'sni_hostname':host}) as response:
            if response.is_redirect:raise ValueError('asset_redirect')
            response.raise_for_status()
            mime=response.headers.get('content-type','').split(';')[0].strip().lower()
            if mime not in {'image/jpeg','image/jpg','image/png','image/webp'}:raise ValueError('asset_mime')
            content=bytearray()
            async for block in response.aiter_bytes():
                content.extend(block)
                if len(content)>MAX_IMAGE_BYTES:raise ValueError('asset_size')
    raw=bytes(content);_validated_image(raw,mime)
    return raw,mime


def _candidates(row):
    sku=parameter_sku(row)
    if not _identity(row):return []
    galleries=[];details=[];seen=set()
    for asset in row.get('assets') or []:
        if not isinstance(asset,dict) or str(asset.get('spu_id') or '')!=row['spu_id'] or asset.get('media_type','image')!='image':continue
        scope=asset.get('scope');bound=str(asset.get('sku_id') or '')
        if scope in {'sku_gallery','selected_sku_gallery'} and sku and bound==sku:target=galleries
        elif scope=='product_detail' and (not bound or bound==sku):target=details
        else:continue
        url=asset.get('image_url')
        if not safe_asset_url(url) or url in seen:continue
        seen.add(url);target.append(asset)
    # Prefer a handful of the actual selected-SKU views, then common detail;
    # this is a per-task delivery budget, not the archived catalog's coverage.
    return galleries[:4]+details[:2]+galleries[4:]+details[2:]


def restore_asset(asset_id, *, root=None):
    match=re.fullmatch(r'vmall-([0-9]{1,32})-([a-f0-9]{64})',str(asset_id))
    if not match:return None
    spu,digest=match.groups();base=(Path(root or ROOT)/'runtime/official-assets').resolve()
    media_root=(base/'vmall').resolve()
    if not media_root.is_relative_to(base):return None
    for suffix in ('.jpg','.png','.webp'):
        path=(media_root/spu/(digest+suffix)).resolve()
        if not path.is_relative_to(media_root) or not path.is_file() or path.stat().st_size>MAX_IMAGE_BYTES:continue
        try:
            if _validated_image(path.read_bytes())[0]==digest:return path
        except (OSError,ValueError):continue
    return None


async def prepare_assets(row, product, *, max_assets=6, fetcher=None, root=None):
    if not _identity(row):return [],[],{}
    candidates=_candidates(row)[:min(max(max_assets,0),12)]
    base=(Path(root or ROOT)/'runtime/official-assets').resolve()
    directory=(base/'vmall'/row['spu_id']).resolve()
    if not directory.is_relative_to(base):return [],[],{}
    index_path=directory/('_index-'+row['source_sha256']+'.json')
    cached={}
    try:
        if index_path.is_file() and index_path.stat().st_size<1_000_000:
            index=json.loads(index_path.read_text(encoding='utf-8'))
            if index.get('source_sha256')==row['source_sha256'] and isinstance(index.get('items'),dict):
                cached=index['items']
    except (OSError,ValueError,AttributeError):pass
    written={}
    source_id='vmall-image-'+hashlib.sha256((row['source_url']+row['source_sha256']).encode()).hexdigest()[:24]
    semaphore=asyncio.Semaphore(2)
    async def prepare(item):
        async with semaphore:
            try:
                expected=item.get('sha256') or cached.get(item['image_url']) or ''
                if expected and (not isinstance(expected,str) or not re.fullmatch(r'[a-f0-9]{64}',expected)):return None
                path=None;raw=None
                if expected:
                    path=restore_asset('vmall-'+row['spu_id']+'-'+expected,root=root)
                    if path:raw=await asyncio.to_thread(path.read_bytes)
                if raw is None:
                    raw,mime=await (fetcher or download_asset)(item['image_url'])
                    digest,suffix=_validated_image(raw,mime)
                    if expected and digest!=expected:return None
                    directory.mkdir(parents=True,exist_ok=True)
                    path=directory/(digest+suffix)
                    await asyncio.to_thread(path.write_bytes,raw)
                digest,_=_validated_image(raw)
                written[item['image_url']]=digest
                title=product+(' / 商城当前规格商品图' if item['scope']!='product_detail' else ' / 商城商品详情素材')
                observation=review_asset_bytes(raw,title)
                if observation.get('usable') is False:return None
                is_detail=item['scope']=='product_detail'
                aid='vmall-'+row['spu_id']+'-'+digest
                # Actual byte review remains usable metadata. Acquisition never
                # upgrades a long detail composition to an isolated product.
                asset=Asset(id=aid,product=product,source_id=source_id,url='/api/assets/'+aid,
                    title=observation.get('title',title),original_title=title,
                    kind='official_detail' if is_detail else 'official_gallery',
                    visual_role='feature_detail' if is_detail else observation.get('visual_role','unknown'),
                    semantic_basis=observation.get('semantic_basis','title_only'),
                    semantic_tags=observation.get('semantic_tags',[]),
                    visual_group='vmall:'+row['spu_id']+':'+(str(item.get('sku_id') or '') if not is_detail else 'common-detail'),
                    claim_scope='商城来源与实际图片字节已绑定；不是拍摄样张、用户亲历或独立透明产品主体，画面不能证明参数或效果。',
                    provenance='vmall_storefront',verified=True,sha256=digest,captured_at=row.get('captured_at',''))
                return asset,path
            except asyncio.CancelledError:raise
            except (OSError,ValueError,httpx.HTTPError,asyncio.TimeoutError,Image.DecompressionBombError):return None
    values=await asyncio.gather(*(prepare(item) for item in candidates))
    assets=[];paths={};seen=set()
    for item in values:
        if item and item[0].sha256 not in seen:
            asset,path=item;assets.append(asset);paths[asset.id]=path;seen.add(asset.sha256)
    if written:
        directory.mkdir(parents=True,exist_ok=True)
        index={'source_sha256':row['source_sha256'],'items':{**cached,**written}}
        try:
            await asyncio.to_thread(index_path.write_text,json.dumps(index,ensure_ascii=False),encoding='utf-8')
        except OSError:
            pass  # Optional index failure cannot invalidate saved image bytes.
    sources=[Source(id=source_id,title=_source_title(row,product,'商品图'),provider='vmall_storefront',url=row['source_url'],
                    sha256=row['source_sha256'],captured_at=row.get('captured_at',''))] if assets else []
    return assets,sources,paths
