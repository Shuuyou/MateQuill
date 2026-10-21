"""Portable, escaped HTML cards. Uses existing opaque asset routes, never raw URLs."""
from __future__ import annotations
import html
import re
import base64
from pathlib import Path


async def embed_card_assets(record):
    from .api import asset
    allowed = {a['id'] for a in record.get('assets',[]) if isinstance(a,dict) and a.get('id')}
    wanted = {aid for card in (record.get('creative_plan') or {}).get('cards',[]) for aid in card.get('asset_ids',[]) if aid in allowed}
    wanted.update(image.get('asset_id') for image in record.get('images', [])
                  if isinstance(image, dict) and image.get('asset_id') in allowed)
    result = {}
    total = 0
    for aid in sorted(wanted):
        if not re.fullmatch(r'[A-Za-z0-9_-]{1,120}',str(aid)):
            continue
        try:
            from .tenancy import get_context
            context = get_context()
            if context and not context.is_legacy and record.get('run_id'):
                from .api import run_asset
                response = await run_asset(record['run_id'], aid)
            else:
                response = await asset(aid)
            raw = Path(response.path).read_bytes() if hasattr(response,'path') else response.body
            mime = response.media_type
            if mime not in ('image/png','image/jpeg','image/webp') or len(raw)>12*1024*1024 or total+len(raw)>24*1024*1024:
                continue
            total += len(raw)
            result[aid]='data:'+mime+';base64,'+base64.b64encode(raw).decode('ascii')
        except Exception:
            # An unavailable asset must not trigger a signed URL or local-path fallback.
            continue
    return result


def render_creative_html(record, embedded_assets=None):
    escape = lambda value: html.escape(str(value or ''), quote=True)
    plan = record.get('creative_plan') or {}
    cards = plan.get('cards') or []
    assets = {a['id']: a for a in record.get('assets', []) if isinstance(a, dict) and a.get('id')}
    approved = record.get('status') == 'ready'
    notice = '已通过系统核验；发布前人工确认素材授权与动态信息。' if approved else '演示／待核验草稿，不可直接发布'
    body = []
    for index, card in enumerate(cards):
        pictures = []
        for aid in card.get('asset_ids') or []:
            if aid not in assets or not re.fullmatch(r'[A-Za-z0-9_-]{1,120}', str(aid)):
                continue
            embedded = (embedded_assets or {}).get(aid)
            if embedded:
                pictures.append('<figure><img src="'+escape(embedded)+'" alt="'+escape(assets[aid].get('title'))+'"><figcaption>'+escape(assets[aid].get('title'))+'</figcaption></figure>')
            else:
                pictures.append('<figure><figcaption>素材未嵌入：'+escape(assets[aid].get('title'))+'；此页面保留配图缺口。</figcaption></figure>')
        disclosures = ''.join('<p>'+escape(x)+'</p>' for x in card.get('disclosures', []))
        body.append('<section class="card"><div class="page-label">'+str(index+1)+' · '+escape(card.get('role'))+'</div><h2>'+escape(card.get('headline'))+'</h2><div class="card-body">'+escape(card.get('body'))+'</div>'+''.join(pictures)+('<aside class="disclosures">适用说明：'+disclosures+'</aside>' if disclosures else '')+'<footer>'+escape(notice)+'</footer></section>')
    # Keep the audited original copy and all its required disclosures with the cards.
    original = ''.join('<p>'+escape(block.get('text'))+'</p>'+''.join('<small class="source-note">'+escape(x)+'</small>' for x in block.get('disclosures',[])) for block in record.get('blocks',[]))
    # A selected document cover can be independent of the model's storyboard.
    # Keep the actual original image, without inventing a card or card claim.
    card_asset_ids = {aid for card in cards for aid in card.get('asset_ids', [])}
    document_pictures = []
    seen = set()
    for placement in record.get('images', []):
        if not isinstance(placement, dict):
            continue
        aid = placement.get('asset_id')
        if aid in seen or aid in card_asset_ids or aid not in assets:
            continue
        seen.add(aid)
        embedded = (embedded_assets or {}).get(aid)
        if embedded and re.fullmatch(r'[A-Za-z0-9_-]{1,120}', str(aid)):
            caption = placement.get('caption') or assets[aid].get('title')
            document_pictures.append('<figure class="document-picture"><img style="max-width:100%;max-height:600px;object-fit:contain" src="'+escape(embedded)+'" alt="'+escape(caption)+'"><figcaption>'+escape(caption)+'</figcaption></figure>')
    original = ''.join(document_pictures) + original
    connector = escape(plan.get('connector_id'))+' / '+escape(plan.get('connector_version'))
    return '<!doctype html><html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>'+escape(record.get('title'))+'</title><style>body{background:#eef1e8;color:#253a30;font:17px/1.85 "Microsoft YaHei",sans-serif;margin:0}main{max-width:1050px;margin:30px auto;padding:20px}.notice{background:#fff4d9;padding:16px;border-radius:10px}.cards{display:grid;grid-template-columns:repeat(auto-fit,minmax(280px,1fr));gap:22px}.card{background:#fffdf6;border:1px solid #d3deca;border-radius:15px;padding:30px;overflow-wrap:anywhere;display:flex;flex-direction:column}.page-label,footer,figcaption{font-size:12px;color:#5a6e60}h1{font-size:29px}h2{font-size:28px;line-height:1.5}.card-body{white-space:pre-wrap}.card img{max-width:100%;max-height:350px;object-fit:contain}figure{margin:20px 0}.disclosures,.source-note{font-size:13px;line-height:1.7;display:block;margin:12px 0}.disclosures{border-top:1px solid #ddd}footer{margin-top:auto;padding-top:20px}.original{background:white;padding:30px;margin-top:25px;border-radius:12px}@media print{body{background:white}.cards{display:block}.card{break-inside:avoid;margin:20px 0}}</style></head><body><main><div class="notice">'+escape(notice)+'</div><h1>'+escape(record.get('title'))+'</h1><p>创意连接器：'+connector+'</p><div class="cards">'+''.join(body)+'</div><article class="original"><h2>正文与适用说明</h2><p>'+escape(record.get('intro'))+'</p>'+original+'<p>'+escape(record.get('cta'))+'</p></article></main></body></html>'
