"""Read-only ima adapter. No SDK, shell subprocess, or writable ima APIs."""
import asyncio
import csv
import hashlib
import io
import ipaddress
import re
import socket
import time
from urllib.parse import urljoin, urlparse

import httpx

from .config import ROOT, settings
from .facts import asset_semantics, asset_scene_match, rank_assets_for_scene
from .visual_assets import review_asset_bytes


class ImaError(RuntimeError):
    def __init__(self,message,code=None,reason='unknown'):
        super().__init__(message)
        self.code=code
        self.reason=reason
        self.safe_message=message


def _normalize(value):
    return re.sub(r'[\s_（）()\-·]', '', value).casefold()


class ImaProvider:
    ALLOWED = {'search_knowledge_base', 'get_knowledge_base', 'get_knowledge_list',
               'search_knowledge', 'get_media_info','get_addable_knowledge_base_list'}

    def __init__(self, config=None):
        self.config = config or settings
        self.kb_id = None
        self.directory = []
        self.assets = {}
        self.last_probe = None
        self._indexed_at = 0
        self._index_cache = {}
        self._local_identities = None
        self._asset_locator_cache = {}
        self._fact_file_rows = None
        self._fact_file_checked = set()
        self._fact_documents = {}
        self._fact_file_cached_at = 0
        self._fact_lookup_lock = asyncio.Lock()
        self._seeded_documents = {}

    async def call(self, endpoint, body):
        if endpoint not in self.ALLOWED:
            raise ImaError('该接口不在只读白名单内')
        if not self.config.ima_ready:
            raise ImaError('尚未配置 ima 凭证；请在本机设置或通过服务器环境变量配置')
        headers = {'ima-openapi-clientid': self.config.ima_client_id,
                   'ima-openapi-apikey': self.config.ima_api_key,
                   'ima-openapi-ctx': 'skill_version=1.1.10'}
        try:
            async with httpx.AsyncClient(timeout=20, follow_redirects=False) as client:
                resp = await client.post('https://ima.qq.com/openapi/wiki/v1/' + endpoint,
                                         json=body, headers=headers)
                if resp.status_code == 429:
                    raise ImaError('ima 请求限流，请稍后重试',429,'rate_limit')
                resp.raise_for_status()
                payload = resp.json()
        except ImaError:
            raise
        except httpx.HTTPStatusError as exc:
            raise ImaError(f'ima HTTP {exc.response.status_code}；请检查服务权限或网络状态') from None
        except httpx.TimeoutException:
            raise ImaError('ima 请求超时，请稍后重试') from None
        except Exception:
            raise ImaError('ima 网络请求或响应解析失败') from None
        if payload.get('code') != 0:
            # Do not echo arbitrary upstream strings, which may contain URLs or secrets.
            code = str(payload.get('code', 'missing'))
            safe_code = code if re.fullmatch(r'-?\d{1,12}|missing', code) else 'unknown'
            upstream_message=str(payload.get('msg') or payload.get('errmsg') or '')
            reason='business'
            if any(phrase in upstream_message for phrase in ('资料获取次数已达上限','今日额度已用尽','每日额度已用尽','今日调用次数已达上限')):
                reason='quota_exhausted'
                message=f'ima 该接口资料获取次数已达上限，请明天再尝试（业务码 {safe_code}）'
            else:
                message=f'ima 业务接口返回失败（业务码 {safe_code}；请检查接口权限、配额及配置）'
            raise ImaError(message,int(code) if re.fullmatch(r'-?\d{1,12}',code) else None,reason)
        return payload.get('data') or {}

    async def connect(self):
        if self.kb_id:
            return self.kb_id
        configured=getattr(self.config,'ima_kb_id','').strip()
        if configured:
            self.kb_id=configured
            return self.kb_id
        cursor = ''
        search_error=None
        for _ in range(10):
            try:
                data = await self.call('search_knowledge_base', {'query': self.config.ima_kb_name,
                                                               'cursor': cursor, 'limit': 20})
            except ImaError as exc:
                search_error=exc
                break
            for entry in data.get('info_list', []):
                if (entry.get('kb_name') or entry.get('name')) == self.config.ima_kb_name:
                    self.kb_id = entry.get('kb_id') or entry.get('id')
                    if self.kb_id:
                        return self.kb_id
            cursor = data.get('next_cursor', '')
            if data.get('is_end') or not cursor:
                break
        if search_error:
            # This documented list contains writable bases, not all shared read-only bases.
            # It is only a bounded discovery attempt; never claim it guarantees access.
            try:
                data=await self.call('get_addable_knowledge_base_list',{'cursor':'','limit':50})
                for entry in data.get('addable_knowledge_base_list',[]):
                    if (entry.get('kb_name') or entry.get('name'))==self.config.ima_kb_name:
                        self.kb_id=entry.get('kb_id') or entry.get('id')
                        if self.kb_id:
                            return self.kb_id
            except ImaError:
                pass
            raise ImaError(str(search_error)+'；可在本机私有配置 IMA_KB_ID 直接访问已授权知识库')
        raise ImaError('当前账号未找到指定共享知识库，请使用已加入该库的同一账号')

    async def index(self, force=False, product=''):
        await self.connect()
        scope=_normalize(product) or '__catalog__'
        cached=self._index_cache.get(scope)
        if cached and not force and time.monotonic()-cached[0]<300:
            return cached[1]
        rows, queue, seen_folders = [], [('', '')], set()
        started=time.monotonic()
        request_count=0
        while queue and len(rows) < self.config.max_directory_items:
            if request_count>=48 or time.monotonic()-started>35:
                break
            folder, parent = queue.pop(0)
            if folder in seen_folders:
                continue
            seen_folders.add(folder)
            cursor, seen_cursors = '', set()
            for _ in range(40):
                body = {'knowledge_base_id': self.kb_id, 'cursor': cursor, 'limit': 50}
                if folder:
                    body['folder_id'] = folder
                if request_count>=48 or time.monotonic()-started>35:
                    break
                request_count+=1
                try:
                    data = await self.call('get_knowledge_list', body)
                except ImaError:
                    if not folder:
                        raise
                    break  # a guessed directory/forbidden subtree must not stall all other paths
                entries = data.get('knowledge_list', [])
                for row in entries:
                    title = row.get('title') or row.get('name') or ''
                    mid = row.get('media_id') or row.get('folder_id')
                    if not mid:
                        continue
                    path = f'{parent}/{title}'.strip('/')
                    item = {**row, 'title': title, 'media_id': mid, 'path': path}
                    rows.append(item)
                    # Folder entries are commonly represented by media_id. Avoid guessing
                    # opaque ID prefixes; use type/metadata, or extension-free directory names.
                    kind = row.get('media_type', row.get('type'))
                    is_folder = (bool(row.get('folder_id')) or row.get('is_folder') is True
                                 or kind in (0, 'folder', 'directory')
                                 or ('file_number' in row or 'folder_number' in row)
                                 or ('.' not in title and bool(title)))
                    if is_folder:
                        # Discover P1 first. P6 marketing drafts, policy/history and teaching
                        # cases cannot establish product facts, so never crawl them here.
                        descend=True
                        if not parent:
                            descend='P1-' in title
                        elif 'P1-' in path and len(path.split('/'))==2:
                            descend=not title.startswith(('00-','06-','07-','08-','99-','90-'))
                        elif product and re.search(r'MateBook|擎云|HUAWEI|华为智选',title,re.I):
                            left,right=_normalize(title),_normalize(product)
                            descend=bool(left and (left in right or right in left))
                        if descend:
                            queue.append((mid, path))
                    if len(rows) >= self.config.max_directory_items:
                        break
                cursor = data.get('next_cursor', '')
                if data.get('is_end') or not cursor or cursor in seen_cursors:
                    break
                seen_cursors.add(cursor)
        self.directory, self._indexed_at = rows, time.monotonic()
        self._index_cache[scope]=(self._indexed_at,rows)
        return rows

    async def probe(self):
        await self.connect()
        route='search_knowledge' if getattr(self.config,'ima_kb_id','') else 'get_knowledge_list'
        note=('搜索接口连接成功；目录接口未测试，全文与图片仍须实际任务核验'
              if route=='search_knowledge' else '目录连接成功；全文、图片及型号链仍须实际任务核验')
        try:
            body={'knowledge_base_id':self.kb_id,'query':'MateBook','cursor':''} if route=='search_knowledge' else {'knowledge_base_id':self.kb_id,'cursor':'','limit':20}
            data = await self.call(route,body)
        except ImaError:
            if route=='search_knowledge':
                raise
            route='search_knowledge'
            data=await self.call(route,{'knowledge_base_id':self.kb_id,'query':'MateBook','cursor':''})
            note='目录不可用，搜索接口连接成功；不代表所有接口额度可用，全文与图片待任务核验'
        self.last_probe = {'ok': True, 'knowledge_base': self.config.ima_kb_name,
                           'root_items': len(data.get('knowledge_list', data.get('info_list',[]))),
                           'read_only': True, 'schema_fields': sorted(data.keys()),
                           'route':route,'note':note}
        return self.last_probe

    def local_identities(self):
        """Read names/OS/directory metadata only. No local parameter is factual evidence."""
        if self._local_identities is not None:
            return self._local_identities
        identities=[]
        root=ROOT/'research'/'knowledge'
        for path in root.rglob('产品事实.md') if root.exists() else []:
            if 'P1-' not in str(path):
                continue
            with path.open(encoding='utf-8-sig') as stream:
                header=stream.readline().strip().lstrip('#').strip()
            name=re.sub(r'\s*[-—]\s*产品事实.*$','',header)
            identities.append({'name':name,'system':'鸿蒙' if '鸿蒙电脑' in str(path) else 'Windows/待确认',
                               'locator':str(path.parent.relative_to(root))})
        self._local_identities=identities
        return identities

    def asset_locators(self,product):
        """Local CSV supplies filename-to-SKU metadata only; bytes still come from ima."""
        key=_normalize(product)
        if key in self._asset_locator_cache:
            return self._asset_locator_cache[key]
        locators={}
        root=ROOT/'research'/'knowledge'
        for index in root.rglob('图片索引.csv') if root.exists() else []:
            if 'P1-' not in str(index):
                continue
            with index.open(encoding='utf-8-sig',newline='') as stream:
                for row in csv.DictReader(stream):
                    if _normalize(row.get('产品型号',''))!=key:
                        continue
                    filename=row.get('文件名','').strip()
                    if not filename or '/' in filename or '\\' in filename:
                        continue
                    prefix=re.match(r'^(hwpc_[a-zA-Z0-9-]+)_',filename)
                    if prefix:
                        locators[filename]={'product':product,'prefix':prefix.group(1),
                                            'title':row.get('官网图片说明','') or filename}
        self._asset_locator_cache[key]=locators
        return locators

    @staticmethod
    def title_identity(title):
        name=title.rsplit('/',1)[-1]
        name=re.sub(r'\.(?:md|txt)$','',name,flags=re.I)
        name=re.sub(r'^\d{1,3}[-_、.\s]+','',name)
        return re.sub(r'\s*[-—]\s*产品事实.*$','',name).strip()

    def search_candidate(self,row,product):
        title=row.get('title','')
        if re.search(r'亮点|不足|竞品|营销|话术|案例|^MC[-_]|评测|对比',title,re.I):
            return False
        if not title.lower().endswith(('.md','.txt')):
            return False
        if title in ('产品事实.md','产品参数.md','规格参数.md','article.md'):
            return True  # fetch only; original product identity and structure still must pass
        identity=self.title_identity(title)
        return _normalize(identity)==_normalize(product)

    def validate_product_document(self,text,product,row):
        # A title match alone is insufficient. Require the remote document itself to
        # identify the selected SKU, plus an explicit facts/specification structure.
        first_heading=re.search(r'(?m)^#{1,2}\s+(.+)$',text)
        heading=self.title_identity(first_heading.group(1)) if first_heading else ''
        identity_ok=_normalize(heading)==_normalize(product)
        specs=len(re.findall(r'(?m)^#{2,3}\s+(?:屏幕|处理器|内存|存储|接口|电池|操作系统|尺寸|重量|特色应用)',text))
        explicit='产品事实' in (first_heading.group(1) if first_heading else '')
        if re.search(r'(?m)^#{1,3}\s*(?:官网公开定位摘要|建库状态|完整档案补抓与内容应用|动态信息边界)',text):
            return False  # expanded discovery cards / marketing templates are not full facts
        if identity_ok and (explicit or specs>=2):
            return True
        # Directory-bound article.md is a supported documented export format; require
        # actual parameter rows rather than using search snippets as product facts.
        return ('P1-' in row.get('path','') and row.get('title','').endswith(('article.md','产品事实.md'))
                and any(_normalize(part)==_normalize(product) for part in row.get('path','').split('/'))
                and bool(re.search(r'(?m)^\s*(?:\|[^\n]+\||[^\n]{2,24}[：:][^\n]+)',text)))

    async def document_images(self,text,product,source_id,query=''):
        found=[]
        embeds=re.findall(r'!\[([^\]]*)\]\(\s*(https://[^\s)]+)(?:\s+"[^"]*")?\s*\)',text)
        embeds.sort(key=lambda entry:-asset_scene_match(entry[0],query)['score'])
        for alt,url in embeds[:8]:
            host=(urlparse(url).hostname or '').lower()
            hosted=host=='ima.qq.com' or (host.endswith('.myqcloud.com')
                       and (host.startswith('ima-') or '.ima.' in host))
            # Official product-site images remain indirect even when referenced by a doc.
            # Do not promote a random CDN or external website to an ima-hosted asset.
            if not hosted or re.search(r'logo|icon|二维码|头像',alt,re.I):
                continue
            try:
                content,mime=await self.read_url(url)
            except (ImaError,httpx.HTTPError):
                continue
            valid=(mime=='image/jpeg' and content.startswith(b'\xff\xd8\xff')
                   or mime=='image/png' and content.startswith(b'\x89PNG\r\n\x1a\n')
                   or mime=='image/webp' and content.startswith(b'RIFF') and content[8:12]==b'WEBP')
            if not valid:
                continue
            aid=hashlib.sha256((source_id+url).encode()).hexdigest()[:20]
            self.assets[aid]=(content,mime)
            bound=_normalize(product) in _normalize(alt)
            found.append({'id':aid,'product':product,'source_id':source_id,'url':'/api/assets/'+aid,
                          'title':alt or 'ima文档内嵌图片（型号待确认）',
                          'provenance':'ima_document_embedded','verified':bound})
            if len(found)>=6:
                break
        return found

    async def fact_document(self,product):
        """Bounded filename discovery; identical filenames are disambiguated by originals."""
        async with self._fact_lookup_lock:
            key=_normalize(product)
            if key in self._seeded_documents:
                return self._seeded_documents[key]
            if time.monotonic()-self._fact_file_cached_at>300:
                self._fact_file_rows=None
                self._fact_file_checked.clear()
                self._fact_documents.clear()
            if key in self._fact_documents:
                return self._fact_documents[key]
            if self._fact_file_rows is None:
                try:
                    result=await self.call('search_knowledge',{'knowledge_base_id':self.kb_id,
                                                 'query':'产品事实.md','cursor':''})
                except ImaError:
                    return None
                self._fact_file_rows=[row for row in result.get('info_list',[])
                                      if row.get('title')=='产品事实.md' and row.get('media_id')][:10]
                self._fact_file_cached_at=time.monotonic()

            async def inspect(row):
                try:
                    content,mime=await self.media(row)
                    if not mime.startswith('text/'):
                        return None
                    text=content.decode('utf-8-sig',errors='replace')[:60000]
                    heading=re.search(r'(?m)^#{1,2}\s+(.+)$',text)
                    if not heading:
                        return None
                    identity=self.title_identity(heading.group(1))
                    if not self.validate_product_document(text,identity,row):
                        return None
                    return _normalize(identity),{**row,'_fact_candidate':True,
                                                    '_cached_media':(content,mime)}
                except (ImaError,httpx.HTTPError):
                    return None

            pending=[row for row in self._fact_file_rows if row['media_id'] not in self._fact_file_checked]
            for offset in range(0,len(pending),2):
                batch=pending[offset:offset+2]
                results=await asyncio.gather(*(inspect(row) for row in batch))
                self._fact_file_checked.update(row['media_id'] for row in batch)
                for result in results:
                    if result:
                        identity,row=result
                        # Multiple originals for one canonical SKU need explicit resolution.
                        previous=self._fact_documents.get(identity)
                        if previous and previous['_cached_media'][0]!=row['_cached_media'][0]:
                            self._fact_documents.pop(identity,None)
                            continue
                        self._fact_documents[identity]=row
                if key in self._fact_documents:
                    return self._fact_documents[key]
            return None

    def seed_documents(self,entries):
        """Trusted-process-only RAM bootstrap for originals previously fetched from ima.

        No HTTP endpoint exposes this method. The controller must supply actual ima
        responses, never local ZIP facts. Bodies are rechecked for exact identity,
        fact-document structure, optional SHA-256, size and text format.
        """
        accepted=[]
        for entry in entries[:20]:
            if isinstance(entry,(tuple,list)) and len(entry)==2:
                row,text=entry
            elif isinstance(entry,dict):
                row,text=entry.get('row',{}),entry.get('text','')
            else:
                continue
            if not isinstance(row,dict) or not isinstance(text,str) or not row.get('media_id'):
                continue
            if row.get('title')!='产品事实.md' or row.get('media_type') not in (7,'7',None):
                continue
            content=text.encode('utf-8')
            if len(content)>self.config.max_media_bytes:
                continue
            digest=hashlib.sha256(content).hexdigest()
            claimed=entry.get('sha256') if isinstance(entry,dict) else None
            if claimed and claimed!=digest:
                continue
            heading=re.search(r'(?m)^#{1,2}\s+(.+)$',text)
            if not heading:
                continue
            identity=self.title_identity(heading.group(1))
            if not self.validate_product_document(text,identity,row):
                continue
            key=_normalize(identity)
            self._seeded_documents[key]={**row,'_fact_candidate':True,
                '_cached_media':(content,'text/markdown'),'_sha256':digest,
                '_cached_at':time.time(),'_cache_origin':'controller_prior_ima_response'}
            accepted.append(identity)
        return {'accepted':len(accepted),'products':accepted,'storage':'process_memory_only'}

    @staticmethod
    def _safe_url(url):
        if any(ord(char)<33 for char in url):
            raise ImaError('素材地址包含非法字符')
        parsed = urlparse(url)
        host = (parsed.hostname or '').lower()
        allowed = ('ima.qq.com', 'mp.weixin.qq.com', 'myqcloud.com', 'huawei.com',
                   'huawei.cn', 'vmall.com', 'hicloud.com', 'hwcdn.net', 'huawei.com.cn')
        if (parsed.scheme != 'https' or parsed.username or parsed.password
                or parsed.port not in (None, 443)
                or not any(host == x or host.endswith('.' + x) for x in allowed)):
            raise ImaError('素材地址不在受信任来源内，已停止读取')
        return host

    async def _resolve_public(self,host):
        """Resolve once, reject any private answer, then pin TCP to an approved IP."""
        try:
            addresses=await asyncio.wait_for(asyncio.get_running_loop().getaddrinfo(
                host,443,type=socket.SOCK_STREAM),timeout=5)
            ips=list(dict.fromkeys(record[4][0] for record in addresses))
            if not ips or any(not ipaddress.ip_address(ip).is_global for ip in ips):
                raise ImaError('素材域名解析至非公网地址，已停止读取')
            return ips
        except ImaError:
            raise
        except (OSError,ValueError,asyncio.TimeoutError):
            raise ImaError('素材域名无法安全解析') from None

    async def read_url(self, url, headers=None):
        current, auth_headers = url, headers or {}
        async with httpx.AsyncClient(timeout=20, follow_redirects=False,trust_env=False) as client:
            for _ in range(4):
                host = self._safe_url(current)
                addresses=await self._resolve_public(host)
                parsed=httpx.URL(current)
                # httpcore supports sni_hostname; this keeps hostname certificate validation
                # while connecting to the exact public IP we just approved, closing DNS TOCTOU.
                pinned=parsed.copy_with(host=addresses[0])
                forwarded={key:value for key,value in auth_headers.items()
                           if key.lower() not in ('host','connection','transfer-encoding','content-length')
                           and not key.lower().startswith('ima-openapi-')}
                forwarded['Host']=host
                # An IP may serve several permitted hosts; do not reuse a TLS connection
                # authenticated for the previous host after a redirect to another hostname.
                forwarded['Connection']='close'
                async with client.stream('GET',pinned,headers=forwarded,
                                         extensions={'sni_hostname':host}) as resp:
                    if resp.status_code in (301, 302, 303, 307, 308):
                        target = urljoin(current, resp.headers.get('location', ''))
                        if self._safe_url(target) != host:
                            auth_headers = {}  # never forward source credentials across hosts
                        current = target
                        continue
                    resp.raise_for_status()
                    mime = resp.headers.get('content-type', '').split(';')[0].lower()
                    content = bytearray()
                    async for block in resp.aiter_bytes():
                        content.extend(block)
                        if len(content) > self.config.max_media_bytes:
                            raise ImaError('素材超过读取大小限制')
                    return bytes(content), mime
        raise ImaError('素材跳转次数超限')

    async def media(self, row):
        info = await self.call('get_media_info', {'media_id': row['media_id']})
        link = info.get('url_info') or {}
        if not link.get('url'):
            if info.get('media_type') == 11:
                raise ImaError('共享笔记可能受作者权限限制，请在 ima 确认原文访问权限')
            raise ImaError('ima 返回成功但未提供可访问的原文地址')
        return await self.read_url(link['url'], link.get('headers'))

    async def catalog(self):
        locators=self.local_identities()
        if locators:
            return [{'id':item['name'],'name':item['name'],'system':item['system'],
                     'catalog_source':'local_locator_only','verified_live':False} for item in locators]
        rows = await self.index()
        products = []
        seen = set()
        for row in rows:
            path = row.get('path', '')
            if 'P1-' not in path or not path.endswith(('article.md','产品事实.md')):
                continue
            name = path.rsplit('/', 2)[-2]
            if name in seen:
                continue
            seen.add(name)
            products.append({'id': name, 'name': name,
                             'system': 'HarmonyOS' if '鸿蒙' in path else '待核验'})
        return products

    async def get_asset(self, asset_id):
        value = self.assets.get(asset_id)
        if not value:
            raise ImaError('素材不存在或会话已过期')
        return value

    async def retrieve(self, product: str, query: str):
        # Model construction is isolated to allow schema evolution without leaking raw IDs.
        from .models import EvidenceBundle
        await self.connect()
        retrieval_warnings=[]
        matches=[]
        search_error=None
        try:
            search = await self.call('search_knowledge', {'knowledge_base_id': self.kb_id,
                        'query': product, 'cursor': ''})
            matches = list(search.get('info_list') or [])
        except ImaError as exc:
            search_error=exc
            retrieval_warnings.append('搜索接口不可用，改用已授权知识库目录定位；不改变事实来源约束')
        # Flat online exports are common. An exact filename candidate permits fetching
        # its original immediately; a separate exhausted list API must not block search.
        flat_matches=[{**r,'_fact_candidate':True} for r in matches if self.search_candidate(r,product)]
        seeded=self._seeded_documents.get(_normalize(product))
        if seeded:
            flat_matches=[seeded]
            retrieval_warnings.append('事实使用本进程此前真实 ima 响应缓存；未以本地 ZIP 参数替代，也不代表本次重新获取原文')
        image_matches=[]
        asset_locators=self.asset_locators(product)
        if asset_locators:
            prefix=next(iter(asset_locators.values()))['prefix']
            try:
                image_search=await self.call('search_knowledge',{'knowledge_base_id':self.kb_id,
                                                               'query':prefix,'cursor':''})
                for row in image_search.get('info_list',[]):
                    filename=row.get('title','')
                    if filename in asset_locators and row.get('media_type') in (9,'9',None):
                        image_matches.append({**row,'_image_bound':True,
                                              '_image_title':asset_locators[filename]['title']})
            except ImaError:
                retrieval_warnings.append('独立图片搜索暂不可用，未以本地图片替代实时来源')
        rows=[]
        if not flat_matches:
            original=await self.fact_document(product)
            if original:
                flat_matches=[original]
            else:
                try:
                    rows=await self.index(product=product)
                except ImaError as exc:
                    if search_error:
                        raise ImaError('搜索与目录均不可用：'+str(search_error)+'；'+str(exc)) from None
                    raise ImaError('未搜索到准确产品事实资料，且目录暂不可用：'+str(exc)) from None
        key = _normalize(product)
        candidates = [r for r in rows if key and key in _normalize(r.get('path', ''))]
        product_rows = [r for r in candidates if r.get('path','').endswith(('article.md','产品事实.md'))
                        and 'P1-' in r.get('path','')]
        folders = {r['path'].rsplit('/',2)[-2] for r in product_rows}
        exact_folders = {name for name in folders if _normalize(name)==key}
        if len(exact_folders)==1:
            chosen=next(iter(exact_folders))
            candidates=[r for r in candidates if chosen in r.get('path','').split('/')]
        elif len(folders)>1:
            return EvidenceBundle(product=product,conflicts=['型号存在歧义，请明确完整型号与代际：'+'；'.join(sorted(folders)[:8])])
        all_rows, seen = [], set()
        for row in candidates + flat_matches + image_matches:
            mid = row.get('media_id')
            if mid and mid not in seen:
                seen.add(mid)
                all_rows.append(row)
        # Exact directory contexts take precedence over old marketing copy.
        all_rows=[r for r in all_rows if '.' in r.get('title','')]
        all_rows.sort(key=lambda r: ('P1-' not in r.get('path',''),
                                     not r.get('title','').endswith(('article.md','产品事实.md')),
                                     not r.get('title','').lower().endswith(('.png','.jpg','.jpeg','.webp')),
                                     not r.get('title','').endswith('.csv'),
                                     -asset_scene_match(r.get('_image_title') or r.get('title',''),query)['score']))
        sources, facts, assets, warnings = [], [], [], retrieval_warnings
        unreadable_count=0
        image_quota_exhausted=False
        for row in all_rows[:12]:
            if '.' not in row.get('title', ''):
                continue
            if (row.get('_image_bound') or row.get('title','').lower().endswith(('.png','.jpg','.jpeg','.webp'))):
                if asset_semantics(row.get('_image_title') or row.get('title',''))['visual_role']=='decorative':
                    continue
            if image_quota_exhausted and (row.get('_image_bound') or row.get('media_type') in (9,'9')):
                continue
            try:
                content, mime = row.get('_cached_media') or await self.media(row)
            except (ImaError, httpx.HTTPError) as exc:
                if isinstance(exc,ImaError) and exc.reason=='quota_exhausted':
                    warnings.append(str(exc))
                    if row.get('_image_bound') or row.get('media_type') in (9,'9'):
                        image_quota_exhausted=True
                        continue
                warnings.append('部分检索资料无法读取原文，未据此生成事实')
                unreadable_count+=1
                if unreadable_count>=3 and not facts:
                    warnings.append('原文连续不可访问，已停止重复请求，请检查共享笔记作者权限')
                    break
                continue
            public_id = hashlib.sha256((row['media_id'] + product).encode()).hexdigest()[:20]
            if mime in ('image/jpeg', 'image/png', 'image/webp'):
                if not row.get('_image_bound') and not any(_normalize(part)==key for part in row.get('path','').split('/')):
                    warnings.append('有图片无法由目录确认精确型号，已拒绝引用')
                    continue
                if not (content.startswith(b'\xff\xd8\xff') or content.startswith(b'\x89PNG\r\n\x1a\n')
                        or (content.startswith(b'RIFF') and content[8:12]==b'WEBP')):
                    warnings.append('图片内容与声明类型不一致，已拒绝引用')
                    continue
                # media_id/source identity never leave the adapter.
                self.assets[public_id] = (content, mime)
                sources.append({'id': public_id, 'title': row.get('path',row.get('title','产品图')),
                                'provider': 'ima', 'excerpt': '指定 ima 知识库直接返回的图片素材'})
                assets.append({'id': public_id, 'title': row.get('_image_title') or row.get('title','产品图'),
                               'product': product, 'url': '/api/assets/' + public_id,
                               'source_id': public_id,
                               'provenance': 'ima_direct', 'verified': True})
            elif mime.startswith('text/') or row.get('title','').endswith(('.md','.txt','.csv')):
                text = content.decode('utf-8-sig', errors='replace')[:60000]
                path = row.get('path',row.get('title',''))
                if row.get('_cache_origin')=='controller_prior_ima_response':
                    path=product+' / '+path+'（此前真实 ima 响应缓存）'
                sources.append({'id': public_id, 'title': path, 'excerpt': text, 'provider': 'ima'})
                if path.endswith('.csv') and ('图片' in path or '素材' in path):
                    records=list(csv.DictReader(io.StringIO(text)))[:80]
                    records.sort(key=lambda record:-asset_scene_match(record.get('官网图片说明') or record.get('文件名',''),query)['score'])
                    for record in records:
                        model = record.get('产品型号') or record.get('型号') or ''
                        if _normalize(model) != key:
                            continue
                        title=record.get('官网图片说明') or record.get('文件名') or 'ima索引指向的图片'
                        if asset_semantics(title)['visual_role']=='decorative':
                            continue
                        link = (record.get('原始图片URL') or record.get('原始图片Url')
                                or record.get('图片URL') or record.get('图片链接'))
                        if not link:
                            continue
                        try:
                            image_bytes,image_mime = await self.read_url(link)
                        except (ImaError,httpx.HTTPError):
                            continue
                        if image_mime not in ('image/jpeg','image/png','image/webp'):
                            continue
                        if not (image_mime=='image/jpeg' and image_bytes.startswith(b'\xff\xd8\xff')
                                or image_mime=='image/png' and image_bytes.startswith(b'\x89PNG\r\n\x1a\n')
                                or image_mime=='image/webp' and image_bytes.startswith(b'RIFF') and image_bytes[8:12]==b'WEBP'):
                            continue
                        aid = hashlib.sha256((public_id+link).encode()).hexdigest()[:20]
                        self.assets[aid] = (image_bytes,image_mime)
                        assets.append({'id':aid,'product':product,'source_id':public_id,
                                       'url':'/api/assets/'+aid,
                                       'title':title,
                                       'provenance':'ima_index_indirect','verified':False})
                        warnings.append('图片由 ima 索引指向外站，须组委会认可来源链后才能正式放行')
                        if len(assets)>=6:
                            break
                # Restrict factual extraction to the product-parameter directory. Teaching
                # cases and historical marketing copy are never promoted to product facts.
                if self.validate_product_document(text,product,row):
                    from .facts import extract_product_facts
                    facts.extend(f.model_dump() for f in extract_product_facts(text,product,public_id))
                    embedded=await self.document_images(text,product,public_id,query)
                    assets.extend(embedded)
                    if any(not a['verified'] for a in embedded):
                        warnings.append('ima托管内嵌图的型号元数据不完整，未作为已核验图片放行')
                elif row.get('_fact_candidate') or ('P1-' in path and path.endswith(('article.md','产品事实.md'))):
                    sources[-1]['excerpt']=''  # never send unrelated templates to the writer
                    warnings.append('命中文件的原文身份或产品事实结构不足，未提取为事实依据')
        if not facts:
            original=await self.fact_document(product)
            if original:
                content,mime=original['_cached_media']
                text=content.decode('utf-8-sig',errors='replace')[:60000]
                public_id=hashlib.sha256((original['media_id']+product).encode()).hexdigest()[:20]
                from .facts import extract_product_facts
                extracted=extract_product_facts(text,product,public_id)
                if extracted:
                    sources.append({'id':public_id,'title':product+' / 产品事实.md','provider':'ima','excerpt':text})
                    facts.extend(f.model_dump() for f in extracted)
                    assets.extend(await self.document_images(text,product,public_id,query))
                    warnings.append('型号搜索命中的是发现卡，已回退检索并核对真实产品事实.md原文')
        if not sources:
            raise ImaError('未获得该型号可核验的 ima 原文；请确认完整型号、年份及共享库资料权限')
        if not assets:
            warnings.append('未获得可核验的 ima 直接图片链；当前稿件不可按正式图文放行')
        # The registry is keyed by pixels: erroneous filenames must not override
        # an actual observation. Pixel review cannot establish SKU/provenance.
        reviewed=[]
        for asset in assets:
            content=self.assets.get(asset['id'])
            review=review_asset_bytes(content[0],asset.get('title','')) if content else {
                **asset_semantics(asset.get('title','')), 'usable': True}
            if not review['usable']:
                warnings.append('像素审阅已排除不适用素材：'+str(review.get('reason') or '画面不适合作为本产品营销配图'))
                self.assets.pop(asset['id'],None)
                continue
            if review.get('visual_review_warning'):
                warnings.append(review['visual_review_warning'])
            reviewed.append({**asset,**review})
        deduplicated=[]
        seen_content=set()
        seen_visual_groups=set()
        for asset in rank_assets_for_scene(reviewed,query):
            if asset.get('visual_role')=='decorative':
                continue
            content=self.assets.get(asset['id'])
            digest=hashlib.sha256(content[0]).hexdigest() if content else asset['id']
            # Curated same-scene groups catch alternate resolutions/crops. A
            # group never propagates SKU certification across different models.
            group=asset.get('visual_group','').strip()
            scoped_group=(_normalize(asset.get('product','')),group) if group else None
            if digest in seen_content or (scoped_group and scoped_group in seen_visual_groups):
                continue
            seen_content.add(digest)
            if scoped_group:
                seen_visual_groups.add(scoped_group)
            deduplicated.append(asset)
        assets=deduplicated
        if assets and not any(asset_scene_match(asset['title'],query)['score']>0 for asset in assets):
            warnings.append('现有图片仅确认型号与来源，未确认场景相关性；不得为满足平台数量而硬凑配图')
        values_by_label={}
        for fact in facts:
            values_by_label.setdefault(_normalize(fact['label']),set()).add(_normalize(fact['value']))
        conflicts=['同一型号的资料在参数「'+label+'」存在不同取值，请人工确认SKU、年份及限制条件'
                   for label,values in values_by_label.items() if len(values)>1]
        payload = {'product': product, 'facts': facts, 'sources': sources,
                   'assets': assets, 'warnings': list(dict.fromkeys(warnings)), 'conflicts':conflicts,
                   'verified_live': bool(not conflicts and facts)}
        return EvidenceBundle.model_validate(payload)
