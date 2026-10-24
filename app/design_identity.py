"""Server-owned Huawei channel identity, grounded in authorized source material.

The caller performs resource authorization before invoking this pure resolver.
Marketing-page observations inform editorial choices; they are not an official
brand manual. This module never fetches a URL, opens an asset path, or trusts a
publisher identity claimed in task text. It emits internal art-direction context,
not boilerplate to paint onto customer-facing images.
"""
from __future__ import annotations

import base64
from collections import Counter
import colorsys
import hashlib
import io
import re
import unicodedata
from urllib.parse import urlsplit,urlunsplit

from PIL import Image

POLICY_VERSION='huawei-channel-2026-10-22.1'
POLICY_SOURCES=(
    {'id':'huawei-product-design-observation','url':'https://consumer.huawei.com/cn/phones/mate90/',
     'basis':'official_product_marketing_page','use':'产品传播名、产品整体图与局部细节的分工；不是品牌字体或网格规范'},
    {'id':'huawei-computer-design-observation','url':'https://consumer.huawei.com/cn/harmonyos-computer/matebook-pro/',
     'basis':'official_product_marketing_page','use':'产品命名与真实设备、设计细节、生活场景之间的叙事关系'},
    {'id':'huawei-retail-identity','url':'https://consumer.huawei.com/cn/retail/',
     'basis':'official_retail_directory','use':'区分旗舰店、授权体验店及具体门店体验；目录本身不证明任意账号资质'},
    {'id':'huawei-brand-assets','url':'https://consumer.huawei.com/cn/legal/intellectual-property/',
     'basis':'official_intellectual_property_terms','use':'公开页面中的商标展示不自动授予独立商标使用权'},
)

_ROLES={
 'brand':{'voice':'品牌主张与产品设计叙事','focus':'用完整主视觉建立新品记忆，系列保持统一识别，再用真实产品细节兑现。','columns':12,'type':'准确产品名与一个清晰中文主张构成层级，标题可参与构图。'},
 'provincial':{'voice':'区域组织与体验资源','focus':'延续已授权活动的视觉主题，把区域体验任务和门店资源组织成可派生的一组作品。','columns':6,'type':'活动主题、产品名、区域信息有明确层级，本地信息不挤成价目表。'},
 'store':{'voice':'本店可实际提供的体验','focus':'用本店真实素材和一项可体验任务组织画面，让顾客看懂到店可以做什么。','columns':4,'type':'短标题、准确产品名与真实现场形成关系，手机缩略图仍清晰。'},
 'salesperson':{'voice':'店员的自然观察与讲解','focus':'真实照片与一个具体发现优先，可无字；保留自然现场感，轻量标注即可。','columns':2,'type':'照片说得清就不加标题，必要文字像自然注记，不模拟总部公告。'},
}
_ROLE_ALIASES={'official':'brand','brand_official':'brand','regional':'provincial','province':'provincial','partner':'provincial',
    'dealer':'provincial','retail':'store','employee':'salesperson','staff':'salesperson','personal':'salesperson','store_staff':'salesperson'}
_QUALIFICATION_WORDS=re.compile(r'官方|直营|授权|旗舰')
_SHA=re.compile(r'^[0-9a-fA-F]{64}$')
_SUPPLIED_PROVENANCE={'ima_direct','local_archive','contest_material','partner_material','authorized_upload'}


def _data(value):
    if hasattr(value,'model_dump'):return value.model_dump()
    return dict(value) if isinstance(value,dict) else {}


def _public_official_url(value):
    try:
        p=urlsplit(value)
        host=(p.hostname or '').lower()
        if p.scheme!='https' or p.username or p.password or p.port not in (None,443):return ''
        if host!='huawei.com' and not host.endswith('.huawei.com'):return ''
        return urlunsplit((p.scheme,p.netloc,p.path,'',''))
    except (ValueError,TypeError):return ''


def _name_key(value):
    text=unicodedata.normalize('NFKC',str(value or '')).strip()
    text=re.sub(r'^(?:HUAWEI\s*|华为\s*)','',text,flags=re.I)
    return re.sub(r'\s+','',text).casefold()


def _source_rows(record):
    result=_data(record.get('result') or record)
    rows=[_data(row) for row in result.get('sources',[]) if _data(row)]
    # These are authorized references attached by the server, not URLs parsed
    # out of an instruction. Keep them separate from generated body copy.
    for row in record.get('source_refs',[]):
        if _data(row):rows.append(_data(row))
    campaign=_data(record.get('campaign_context'))
    for row in campaign.get('source_refs',[]):
        if _data(row):rows.append(_data(row))
    result_map={}
    for index,row in enumerate(rows):
        url=_public_official_url(row.get('url',''))
        if not url:continue
        identifier=str(row.get('id') or row.get('source_id') or f'official-ref-{index}')
        result_map[identifier]={'id':identifier,'url':url,'title':str(row.get('title',''))[:220],
            'captured_at':str(row.get('captured_at') or row.get('published_at') or '')[:40]}
    return result_map


def _asset_rows(record,assets):
    if assets is None:assets=_data(record.get('result') or record).get('assets',[])
    if isinstance(assets,dict):return {str(key):_data(value) for key,value in assets.items() if _data(value)}
    rows={}
    for value in assets or []:
        row=_data(value);identifier=row.get('id') or row.get('asset_id')
        if identifier:rows[str(identifier)]=row
    return rows


def _asset_source(row,sources):
    source=sources.get(str(row.get('source_id','')),{})
    url=source.get('url') or _public_official_url(row.get('source_url','')) or _public_official_url(row.get('url',''))
    if not url or not row.get('verified'):return None
    if row.get('provenance') not in ('huawei_official','official_web','official_campaign','official_product','huawei_campaign'):return None
    return {'source_id':str(row.get('source_id','')),'url':url,'captured_at':str(row.get('captured_at') or source.get('captured_at') or '')[:40]}


def _observed_palette(row):
    """Sample supplied pixels only; never follow URLs or use claimed color tags."""
    value=row.get('data_url')
    if not isinstance(value,str) or len(value)>12_000_000:return []
    header,separator,encoded=value.partition(',')
    if not separator or header not in ('data:image/png;base64','data:image/jpeg;base64','data:image/webp;base64'):return []
    try:
        raw=base64.b64decode(encoded,validate=True)
        if len(raw)>8_000_000:return []
        with Image.open(io.BytesIO(raw)) as original:
            if original.width*original.height>40_000_000:return []
            image=original.convert('RGBA');image.thumbnail((64,64))
            access=image.load()
            pixels=[list(access[x,y][:3]) for y in range(image.height) for x in range(image.width) if access[x,y][3]>96]
        chromatic=[]
        for rgb in pixels:
            _,s,v=colorsys.rgb_to_hsv(*(c/255 for c in rgb))
            if s>.10 and v>.12:chromatic.append(tuple(rgb))
        sampled=chromatic or [tuple(rgb) for rgb in pixels]
        if not sampled:return []
        buckets=Counter(tuple(min(255,round(c/24)*24) for c in rgb) for rgb in sampled)
        selected=[]
        for rgb,count in buckets.most_common(24):
            if any(sum((a-b)**2 for a,b in zip(rgb,previous))<60**2 for previous,_ in selected):continue
            selected.append((rgb,count))
            if len(selected)>=3:break
        return [{'color':'#'+''.join(f'{c:02X}' for c in rgb),'sample_share':round(count/len(sampled),3)} for rgb,count in selected]
    except (ValueError,OSError,Image.DecompressionBombError):return []


def resolve_design_identity(task,record,assets=None,*,publisher_context=None):
    """Resolve internal identity after caller authorization; performs no I/O.

    `publisher_context` is exclusively server-owned context about the actual
    publishing account, not the campaign issuer or request-body role. Supported
    keys: publisher_identity/publisher_kind, publisher_label/store_name,
    identity_label_verified, brand_account_verified, is_simulation,
    channel_relationship_verified, brand_asset_grants.
    """
    task=_data(task);record=_data(record);context=_data(publisher_context)
    result=_data(record.get('result') or record)
    sources=_source_rows(record);all_assets=_asset_rows(record,assets)
    requested=str(task.get('product') or result.get('product') or '')[:200].strip()
    product_key=_name_key(requested)
    scoped=[];official=[];supplied=[];signals=[];campaign_visuals=[]
    for identifier,row in all_assets.items():
        product=str(row.get('product') or '')
        if not product_key or _name_key(product)!=product_key:continue
        scoped.append(identifier)
        evidence=_asset_source(row,sources)
        if evidence:
            official.append((identifier,row,evidence))
            signals.append({'kind':'official_product_visual','asset_id':identifier,'source_id':evidence['source_id'],
                'source_url':evidence['url'],'captured_at':evidence['captured_at'],
                'authority':'authorized_first_party_asset','view':str(row.get('view') or '')[:24]})
        elif row.get('verified') and row.get('provenance') in _SUPPLIED_PROVENANCE:
            supplied.append((identifier,row,{'source_id':str(row.get('source_id',''))}))
            signals.append({'kind':'supplied_product_visual','asset_id':identifier,'source_id':str(row.get('source_id','')),
                'authority':'authorized_supplied_material','view':str(row.get('view') or '')[:24]})
    campaign_id=context.get('campaign_id') or _data(record.get('campaign_context')).get('campaign_id')
    granted_campaign_ids=context.get('campaign_asset_ids') or []
    if not isinstance(granted_campaign_ids,(list,tuple,set)):granted_campaign_ids=[]
    for identifier,row in all_assets.items():
        if row.get('visual_role') not in ('campaign_key_visual','campaign_visual','campaign_poster') and row.get('kind')!='campaign_key_visual':continue
        same_product=bool(product_key and _name_key(row.get('product'))==product_key)
        same_campaign=bool(campaign_id and row.get('campaign_id')==campaign_id)
        if not same_product and not same_campaign and identifier not in granted_campaign_ids:continue
        evidence=_asset_source(row,sources)
        if not evidence:continue
        campaign_visuals.append((identifier,row,evidence))
        signals.append({'kind':'official_campaign_visual','asset_id':identifier,'source_id':evidence['source_id'],
            'source_url':evidence['url'],'authority':'authorized_first_party_campaign_asset',
            'use':'composition_and_color_reference'})
    canonical=next((str(row['product']) for _,row,_ in [*official,*supplied] if row.get('product')),requested)
    name_evidence=[evidence['source_id'] for _,_,evidence in official if evidence['source_id']]
    for fact in result.get('facts',[]):
        row=_data(fact)
        if _name_key(row.get('product'))!=product_key or row.get('source_id') not in sources:continue
        if not name_evidence:canonical=str(row['product']);name_evidence.append(row['source_id'])
        if any(word in str(row.get('label','')) for word in ('颜色','配色','外观','设计','材质','形态')):
            value=str(row.get('value',''));qualifiers=row.get('qualifiers') or []
            if len(value)>180 or not all(isinstance(q,str) for q in qualifiers) or sum(map(len,qualifiers))>400:continue
            signals.append({'kind':'official_product_design_description','source_id':row['source_id'],
                'label':str(row.get('label',''))[:70],'observed':value,'qualifiers':list(qualifiers),
                'authority':'authorized_product_evidence'})
    role=str(context.get('publisher_identity') or context.get('publisher_kind') or 'store')
    role=_ROLE_ALIASES.get(role,role)
    if role not in _ROLES:role='store'
    profile=_ROLES[role]
    simulation=context.get('is_simulation') is True
    label=str(context.get('publisher_label') or context.get('store_name') or '')[:180]
    verified_brand=role=='brand' and context.get('brand_account_verified') is True and not simulation
    label_verified=context.get('identity_label_verified') is True
    relationship_verified=context.get('channel_relationship_verified') is True
    signoff=label if label and (label_verified or not _QUALIFICATION_WORDS.search(label)) else ''
    if role=='brand' and not verified_brand:signoff=''
    observed=[];palette_assets=[]
    visual_references=list({identifier:(identifier,row,evidence) for identifier,row,evidence in [*campaign_visuals,*official,*supplied]}.values())
    for identifier,row,_ in visual_references[:3]:
        colors=_observed_palette(row)
        if colors:
            palette_assets.append(identifier)
            for item in colors:
                if item['color'] not in [r['color'] for r in observed]:observed.append({**item,'asset_id':identifier})
    selected_colors=[row['color'] for row in observed[:4]]
    palette={'source':'observed_authorized_assets' if observed else 'editorial_inference',
        'colors':selected_colors,'reference_asset_ids':palette_assets,
        'method':'visible_pixel_quantization' if observed else 'task_and_product_editorial_direction',
        'direction':'从真实产品与获准活动主视觉取色，环境可用清晰明暗对比；产品本色不随背景重染。',
        'continuity':'同组固定色彩关系；门店变化来自真实任务与素材，不靠随机换色。'}
    category='device'
    for term,name in (('matebook','computer'),('matestation','computer'),('matepad','tablet'),('freebuds','audio'),('pura','phone'),('mate','phone'),('watch','wearable')):
        if term in canonical.casefold():category=name;break
    category_focus={'computer':'让准确设备、可用的真实界面与一项工作任务建立视觉关系。',
        'tablet':'以准确设备形态与真实内容组织画面，操作界面使用已有素材。',
        'audio':'可见的耳机与充电盒形态、佩戴或随身尺度构成视觉锚点。',
        'phone':'从准确机身轮廓、源图可见的材质与设计细节构成视觉锚点。',
        'wearable':'以真实设备与佩戴尺度建立联系。','device':'以准确产品与真实使用任务建立联系。'}[category]
    brand='HUAWEI' if re.search(r'HUAWEI|华为',canonical,re.I) else 'Huawei channel'
    campaign_signals=[{'kind':'official_source_reference','source_id':row['id'],'title':row['title'],'url':row['url'],
        'authority':'authorized_first_party_reference'} for row in sources.values()][:4]
    grants=context.get('brand_asset_grants') or []
    if not isinstance(grants,list):grants=[]
    authorized_marks=[]
    for grant in grants[:12]:
        grant=_data(grant);identifier=str(grant.get('asset_id',''));row=all_assets.get(identifier,{})
        permission=grant.get('permission_reference');roles=grant.get('publisher_roles')
        if not identifier or not row or not isinstance(permission,str) or not permission.strip() or grant.get('active') is False:continue
        if not isinstance(roles,list) or role not in roles:continue
        if row.get('kind') not in ('logo','brand_mark','campaign_mark') and row.get('visual_role') not in ('brand_logo','brand_mark','campaign_mark'):continue
        evidence=_asset_source(row,sources)
        if not evidence or not _SHA.fullmatch(str(row.get('sha256',''))):continue
        authorized_marks.append({'asset_id':identifier,'source_id':evidence['source_id'],
            'permission_reference':permission[:160],'source_sha256':row['sha256'],'placement_mode':'exact_asset_composite'})
    family_seed=hashlib.sha256((product_key+'|'+ '|'.join(sorted(name_evidence))).encode()).hexdigest()[:12]
    return {'version':POLICY_VERSION,'brand':brand,
        'authority':{'kind':'matequill_editorial_policy','official_brand_manual':False,'research_as_of':'2026-10-22',
            'policy_sources':[dict(row) for row in POLICY_SOURCES]},
        'product_naming':{'canonical_name':canonical,'source':'official_product_evidence' if name_evidence or official else ('supplied_product_evidence' if supplied else 'task_specified'),
            'source_ids':list(dict.fromkeys(name_evidence)),'rule':'按来源保留系列、型号、版本与空格，型号不拆行；产品名与传播主张分别排版。'},
        'publisher':{'role':role,'label':label,'public_signoff':signoff,'voice':profile['voice'],'is_simulation':simulation,
            'official_brand_account_verified':verified_brand,'channel_relationship_verified':relationship_verified,
            'endorsement_claims':[]},
        'principles':[profile['focus'],category_focus,'准确产品名与一个主张共同建立识别，不用红底或Logo数量代替品牌设计。',
            '系列字体、网格与色彩关系一致，跨平台重新组织阅读顺序。','产品图片中的既有标识保持原样，独立品牌标识只使用获准原资产。'],
        'typography':{'headline':'Noto Sans SC','editorial_alternative':'Noto Serif SC','body':'Noto Sans SC',
            'source':'MateQuill editorial font pair','hierarchy':profile['type'],'continuity':'同组固定字体组合与型号排版，不逐页随机换字体。'},
        'palette':palette,'visual_system':{'family_key':family_seed,'product_category':category,'grid_columns':profile['columns'],
            'baseline_unit':8,'grid_authority':'MateQuill editorial recommendation','publisher_adaptation':profile['focus']},
        'source_signals':(signals+campaign_signals)[:10],
        'reference_asset_ids':[identifier for identifier,_,_ in visual_references[:4]],
        'source_asset_ids':scoped[:12],'authorized_brand_assets':authorized_marks,
        'store_reference_asset_ids':[identifier for identifier,row in all_assets.items() if row.get('visual_role') in ('store_photo','user_photo')][:4]}
