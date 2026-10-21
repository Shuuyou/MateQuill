"""Reviewable product onboarding and scoped ecosystem capabilities.

Source ingestion produces a draft first. Only explicitly confirmed documents
join the same evidence pipeline as the collected product specification data.
"""
from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re
import sqlite3
from urllib.parse import urlsplit
from uuid import uuid4

import httpx
from pydantic import BaseModel, ConfigDict, Field
from typing import Literal

from .config import ROOT
from .facts import extract_product_facts
from .official_catalog import extract_specification, extract_product_conditions


class ProductPreviewInput(BaseModel):
    model_config = ConfigDict(extra='forbid')
    name: str = Field(min_length=2, max_length=120)
    category: Literal['computer', 'phone', 'tablet', 'accessory'] = 'computer'
    source_url: str = Field(min_length=8, max_length=1200)
    source_text: str = Field(default='', max_length=200000)


class ProductConfirmInput(BaseModel):
    draft_id: str = Field(min_length=10, max_length=100)
    confirmed: bool


def utc_now():
    return datetime.now(timezone.utc).isoformat()


def read_products(path):
    try:
        document = json.loads(Path(path).read_text(encoding='utf-8-sig'))
        return [row for row in document.get('products', []) if isinstance(row, dict)]
    except (OSError, ValueError, AttributeError):
        return []


class ProductRepository:
    def __init__(self, path=None):
        from .tenancy import get_context
        context = get_context()
        self.scoped = path is None and context is not None and not context.is_legacy
        if self.scoped:
            return
        self.path = Path(path) if path else ROOT/'runtime/knowledge/products.sqlite3'
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as conn:
            conn.execute('CREATE TABLE IF NOT EXISTS schema_version(version INTEGER PRIMARY KEY)')
            conn.execute('INSERT OR IGNORE INTO schema_version VALUES (1)')
            conn.execute('''CREATE TABLE IF NOT EXISTS product_drafts(
                id TEXT PRIMARY KEY, body TEXT NOT NULL, created_at TEXT NOT NULL)''')
            conn.execute('''CREATE TABLE IF NOT EXISTS products(
                name TEXT PRIMARY KEY, body TEXT NOT NULL, updated_at TEXT NOT NULL)''')

    def connect(self):
        conn = sqlite3.connect(str(self.path), timeout=10)
        conn.execute('PRAGMA foreign_keys=ON')
        return conn

    def create_draft(self, body):
        draft_id = 'product-draft-'+uuid4().hex
        body = {**body, 'draft_id': draft_id}
        if self.scoped:
            from .tenancy import save_body
            save_body('product_draft', draft_id, body, visibility='tenant')
            return body
        with self.connect() as conn:
            conn.execute('INSERT INTO product_drafts VALUES(?,?,?)',
                         (draft_id, json.dumps(body, ensure_ascii=False), utc_now()))
        return body

    def confirm(self, draft_id):
        if self.scoped:
            from .tenancy import load_body, save_body
            product = load_body('product_draft', draft_id, action='write')
            if not product:
                raise KeyError(draft_id)
            if product.get('status') == 'confirmed':
                raise KeyError(draft_id)
            product.update(reviewed=True, reviewed_at=utc_now(), status='confirmed')
            identifier = 'product-' + hashlib.sha256(product['name'].encode()).hexdigest()[:24]
            # Same product names in different enterprises remain separate objects.
            from .tenancy import require_context
            identifier = require_context().tenant_id + '-' + identifier
            save_body('product', identifier, product, visibility='tenant')
            save_body('product_draft', draft_id, product, visibility='tenant')
            return product
        with self.connect() as conn:
            row = conn.execute('SELECT body FROM product_drafts WHERE id=?', (draft_id,)).fetchone()
            if not row:
                raise KeyError(draft_id)
            product = json.loads(row[0])
            product.update(reviewed=True, reviewed_at=utc_now(), status='confirmed')
            conn.execute('INSERT OR REPLACE INTO products VALUES(?,?,?)',
                         (product['name'], json.dumps(product, ensure_ascii=False), product['reviewed_at']))
            conn.execute('DELETE FROM product_drafts WHERE id=?', (draft_id,))
        return product

    def list_products(self):
        if self.scoped:
            from .tenancy import list_resources
            return [row['body'] for row in list_resources('product')]
        with self.connect() as conn:
            rows = conn.execute('SELECT body FROM products ORDER BY updated_at DESC').fetchall()
        return [json.loads(row[0]) for row in rows]


def load_products(repository=None):
    rows = read_products(ROOT/'data/official_products.json') + read_products(ROOT/'data/ecosystem_products.json')
    for row in rows:
        row.setdefault('category', 'computer')
        row.setdefault('provider', 'huawei_official')
    rows += (repository or ProductRepository()).list_products()
    by_name = {}
    for row in rows:
        by_name[row.get('product') or row.get('name', '')] = row
    return [row for name, row in by_name.items() if name and row.get('facts')]


def summary(row):
    name = row.get('name') or row['product']
    os_fact = next((f for f in row.get('facts', []) if f.get('label') == '操作系统'), {})
    return {'id': 'product-'+hashlib.sha256(name.encode()).hexdigest()[:16], 'name': name,
            'category': row.get('category', 'computer'), 'system': os_fact.get('value') or row.get('system', ''),
            'source_url': row.get('spec_url') or row.get('source_url') or row.get('product_url', ''),
            'fact_count': len(row.get('facts', [])), 'updated_at': row.get('reviewed_at') or row.get('captured_at', '')}


def validate_source_url(value):
    parsed = urlsplit(value)
    if parsed.scheme != 'https' or not parsed.hostname or parsed.username or parsed.password or parsed.port not in (None, 443):
        raise ValueError('请填写完整的 HTTPS 资料链接')
    if parsed.hostname in ('localhost',) or parsed.hostname.endswith(('.local', '.internal')):
        raise ValueError('请使用公开资料链接')
    return parsed


async def source_preview(payload: ProductPreviewInput, repository=None):
    parsed = validate_source_url(payload.source_url)
    original = payload.source_text.strip()
    is_official = parsed.hostname in ('consumer.huawei.com', 'www.vmall.com', 'vmall.com')
    conditions = []
    if not original:
        # Automatic fetching has a narrow first-party surface. Other sources
        # are onboarded through their explicit text and the same review step.
        if parsed.hostname != 'consumer.huawei.com':
            raise ValueError('请粘贴这份资料的原文，确认后即可用于创作')
        async with httpx.AsyncClient(timeout=25, follow_redirects=False) as client:
            async with client.stream('GET', payload.source_url) as response:
                response.raise_for_status()
                if response.is_redirect:
                    raise ValueError('资料链接已跳转，请使用最终页面地址')
                raw = bytearray()
                async for chunk in response.aiter_bytes():
                    raw.extend(chunk)
                    if len(raw) > 2*1024*1024:
                        raise ValueError('资料过长，请粘贴本次使用的产品原文')
        original = raw.decode('utf-8', errors='replace')
    if '<html' in original[:1000].lower() or '<!doctype' in original[:1000].lower():
        extracted = extract_specification(original, payload.name)
        heading = re.sub(r'\s+', '', extracted.get('page_heading', '')).lower()
        requested = re.sub(r'\s+', '', payload.name).lower()
        if heading and requested not in heading:
            raise ValueError('页面对应的产品名称与输入不同，请确认准确型号后再导入')
        text = extracted['text']
        conditions = extract_product_conditions(original)
        if not extracted['sections']:
            raise ValueError('这个页面没有可提取的规格，请粘贴产品资料原文')
    else:
        text = original if original.startswith('#') else '# '+payload.name+'\n\n'+original
    sid = 'ingest-'+hashlib.sha256((payload.source_url+text).encode()).hexdigest()[:20]
    facts = extract_product_facts(text, payload.name, sid, max_facts=60)
    if not facts:
        raise ValueError('请保留产品属性和值，例如“## 屏幕\n尺寸：14 英寸”，以便确认具体事实')
    records = [dict(f.model_dump(), source_url=payload.source_url) for f in facts]
    system = next((f.value for f in facts if f.label == '操作系统'), '')
    body = {'name': payload.name, 'product': payload.name, 'category': payload.category,
            'system': system, 'source_url': payload.source_url, 'spec_url': payload.source_url,
            'provider': 'huawei_official' if is_official else 'store_knowledge',
            'facts': records, 'excerpt': text, 'conditions': conditions,
            'source_sha256': hashlib.sha256(original.encode()).hexdigest(), 'captured_at': utc_now(),
            'reviewed': False, 'status': 'review', 'aliases': [payload.name.replace('HUAWEI ', '')]}
    from .resource_access import ensure_live_context
    context = ensure_live_context()
    if context and not context.is_legacy:
        from .tenancy import require_role
        require_role('admin', 'operator')
    return (repository or ProductRepository()).create_draft(body)


CLIPBOARD = {
    'id': 'harmony-cross-device-clipboard', 'title': '跨设备剪贴板',
    'excerpt': '使用跨设备剪贴板，在设备间复制粘贴文本、图片等素材，轻松完成多设备间的内容共享。',
    'source_url': 'https://consumer.huawei.com/cn/support/content/zh-cn16046728/',
    'conditions': ['当手机/平板为 HarmonyOS 5.0 及以上系统时：',
                   '在手机/平板、电脑上，开启 WLAN、蓝牙，并登录同一华为账号。',
                   '在手机/平板、电脑上，开启跨设备剪贴板的开关。'],
    'computer_names': ['HUAWEI MateBook 14（鸿蒙版）', 'HUAWEI MateBook Pro',
                       'HUAWEI MateBook Pro S', 'HUAWEI MateBook Fold 非凡大师'],
}


def resolve_ecosystem(products, scene='', audience='', platform='xiaohongshu', repository=None):
    rows = load_products(repository)
    by_name = {r.get('name') or r['product']: r for r in rows}
    selected = list(dict.fromkeys(products))
    unknown = [name for name in selected if name not in by_name]
    if unknown:
        raise ValueError('请先从产品目录选择或导入：'+'、'.join(unknown))
    if not 2 <= len(selected) <= 4:
        raise ValueError('选择 2 到 4 款产品，组合一个具体任务')
    computer = next((name for name in selected if by_name[name].get('category', 'computer') == 'computer'), selected[0])
    companions = [name for name in selected if name != computer]
    capabilities = []
    eligible = []
    if computer in CLIPBOARD['computer_names']:
        for name in companions:
            row = by_name[name]
            version = re.search(r'HarmonyOS\s*(\d+(?:\.\d+)?)', row.get('system', ''), re.I)
            if row.get('category') in ('phone', 'tablet') and version and float(version[1]) >= 5:
                eligible.append(name)
    if eligible:
        capabilities.append({k: v for k, v in CLIPBOARD.items() if k != 'computer_names'})
    task = {'product': computer, 'ecosystem_products': companions, 'audience': audience,
            'scene': scene, 'platform': platform, 'mode': 'local', 'stage': 'consideration',
            'goal': '围绕同一真实任务，介绍已选设备各自作用与有依据的协同步骤',
            'requirements': '已选设备：'+'、'.join(selected)+'。每项参数必须属于对应设备。'+
                ('可围绕跨设备剪贴板介绍任务，保留所附条件。' if capabilities else '围绕多设备各自承担的任务组织内容；协同功能需用户补充对应资料。')}
    return {'status': 'ready' if scene.strip() and audience.strip() else 'clarify',
            'product': computer, 'ecosystem_products': companions, 'capabilities': capabilities,
            'questions': ([] if scene.strip() else ['这些设备一起帮顾客完成什么事？'])+
                         ([] if audience.strip() else ['这次内容主要面向哪类顾客？']), 'task': task}


def capability_fact(primary, companions, rows):
    if primary not in CLIPBOARD['computer_names']:
        return None
    plan = resolve_ecosystem([primary, *companions], '资料整理', '已选顾客', repository=_FixedRows(rows))
    if not plan['capabilities']:
        return None
    from .models import Fact, Source
    cap = CLIPBOARD
    quote = cap['excerpt']+'\n'+'\n'.join(cap['conditions'])
    sid = 'ecosystem-'+hashlib.sha256(cap['source_url'].encode()).hexdigest()[:18]
    source = Source(id=sid, title='华为官方支持 / 鸿蒙电脑跨设备复制粘贴', url=cap['source_url'],
                    provider='huawei_official', excerpt=quote, sha256=hashlib.sha256(quote.encode()).hexdigest(),
                    captured_at='2026-10-20')
    fact = Fact(id='FE'+hashlib.sha256((primary+sid).encode()).hexdigest()[:14], product=primary,
                label='生态协同/跨设备剪贴板', value=cap['excerpt'], quote=quote,
                source_id=sid, qualifiers=cap['conditions'])
    return source, fact


class _FixedRows:
    def __init__(self, rows): self.rows = rows
    def list_products(self): return self.rows
