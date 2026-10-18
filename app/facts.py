"""Conservative extraction of already-selected P1 product documents.

No network, summarization, model guessing or promotion of teaching examples.
The caller is responsible for selecting an authorized product-parameter source.
"""
from __future__ import annotations
import hashlib
import re
from .models import Fact


# These labels describe asset metadata, never verified visual observations. In
# particular a product photograph cannot establish battery life or compatibility.
_VISUAL_TOPICS = {
    'portability': ('轻薄', '便携', '机身', '重量', '厚度', '背包', '携带', '通勤', '出差'),
    'display': ('屏幕', '显示', '触控', '触摸', '护眼', '柔光', 'oled', '色彩', '修图'),
    'keyboard': ('键盘', '触控板', '打字', '输入'),
    'ports': ('接口', '插口', '连接线', '外接', '投屏'),
    'collaboration': ('多屏协同', '跨设备', '互联', '互传', '文件分享', '手机协同', '手机', '平板', '多设备'),
    'document_task': ('文档', '资料', '阅读', '论文', '笔记'),
    'battery': ('电池', '续航', '充电'),
    'performance': ('性能', '处理器', '散热', '游戏', '渲染', '编译'),
    'appearance': ('外观', '配色', '颜色', '质感', '外观设计', '主视觉', '产品全貌'),
    'folding': ('折叠', '展开', '形态', '双屏'),
    'study_scene': ('教室', '课堂', '上课', '图书馆', '宿舍', '自习'),
    'work_scene': ('会议', '办公室', '办公桌', '工位'),
}


def _visual_tags(text: str) -> list[str]:
    value = str(text or '').casefold()
    return [tag for tag, terms in _VISUAL_TOPICS.items() if any(term in value for term in terms)]


def asset_semantics(title: str) -> dict:
    """Conservative, inspectable title metadata; no image model is implied."""
    title = str(title or '')
    tags = _visual_tags(title)
    decorative = bool(re.search(r'logo|icon|二维码|头像|装饰图|品牌标识', title, re.I))
    if decorative:
        role = 'decorative'
    elif any(tag.endswith('_scene') for tag in tags):
        role = 'scene'
    elif any(tag not in ('appearance', 'portability') for tag in tags):
        role = 'feature_detail'
    elif tags:
        role = 'product_overview'
    else:
        role = 'unknown'
    return {'semantic_tags': tags, 'visual_role': role, 'semantic_basis': 'title_only',
            'claim_scope': '仅按素材标题定位主题，未经视觉内容核验；不能据此证明参数、性能或功能效果'}


def asset_scene_match(title: str, query: str) -> dict:
    """Return an explainable relevance hint, not an image/claim approval."""
    meta = asset_semantics(title)
    overlap = sorted(set(meta['semantic_tags']) & set(_visual_tags(query)))
    if meta['visual_role'] == 'decorative':
        return {'score': -100, 'matched_tags': [], 'reason': '品牌标识或装饰素材，不作为营销正文产品配图'}
    if overlap:
        return {'score': 10 * len(overlap), 'matched_tags': overlap,
                'reason': '素材标题与任务主题交集：' + '、'.join(overlap) + '；仍须核对图片可见内容与段落'}
    return {'score': 0, 'matched_tags': [],
            'reason': '素材标题未证明与本场景相关；不能因同型号而自动填入正文'}


def rank_assets_for_scene(assets, query: str):
    """Stable ranking of dicts or Asset objects; preserves source IDs and bytes."""
    def title_of(asset):
        return asset.get('title', '') if isinstance(asset, dict) else getattr(asset, 'title', '')
    return sorted(assets, key=lambda asset: -asset_scene_match(title_of(asset), query)['score'])


def extract_product_facts(text: str, product: str, source_id: str, max_facts: int = 18) -> list[Fact]:
    buckets = []
    headings = list(re.finditer(r'(?m)^#{2,3}\s+([^\r\n]+)\r?$', text))
    excluded = re.compile('来源|包装|清单|参考|价格|优惠|补贴|销量|评价|导航|链接|摘录$')
    labels = {'宽度','深度','高度','长度','厚度','重量','尺寸','类型','屏占比','屏幕比例','分辨率',
              '刷新率','亮度','色域','对比度','可视角度','触摸屏','材料','容量','WLAN','蓝牙','星闪',
              '电源键','音量键','处理器','内存','存储','操作系统','CPU','GPU','NPU','显卡','图形处理器',
              '独立显卡','集成显卡','扬声器','麦克风','色准','色深','峰值亮度','典型亮度','静态刷新率','动态刷新率'}
    for index, heading in enumerate(headings):
        section = heading.group(1).strip()
        if excluded.search(section):
            continue
        end = headings[index+1].start() if index+1 < len(headings) else len(text)
        body = text[heading.end():end].strip()
        # Website chrome is never a product assertion.
        body = re.split(r'(?m)^即将离开|^您即将跳转|^继续访问',body)[0].strip()
        if not body:
            continue
        quote = text[heading.start():end]
        rows = [line.strip() for line in body.splitlines() if line.strip()]
        notes = [line.lstrip('* ').strip() for line in rows if line.startswith('*')]
        content = [line for line in rows if not line.startswith(('*','>','http','#'))]
        candidates = []
        if any(line.startswith('|') for line in content):
            for line in content:
                if not line.startswith('|'):
                    continue
                cells=[x.strip() for x in line.strip('|').split('|')]
                if len(cells)<2 or any('---' in x for x in cells) or cells[0] in {'参数','项目','字段','属性'}:
                    continue
                if excluded.search(cells[0]) or not cells[1]:
                    continue
                candidates.append((cells[0],cells[1]))
        else:
            # Treat field names as labels, and retain multi-line alternatives as
            # one verbatim value instead of inventing several conflicting records.
            current_label=section
            pending=[]
            variant=''
            def flush_pending():
                if pending:
                    first,start,_=pending[0]
                    _,_,end=pending[-1]
                    candidates.append((current_label,body[start:end]))
                    pending.clear()
            for match in re.finditer(r'(?m)^([^\r\n]+)',body):
                line=match.group(1).strip()
                if not line or line.startswith(('*','>','http','#')):
                    continue
                start=match.start()+len(match.group(1))-len(match.group(1).lstrip())
                end=match.end()-len(match.group(1))+len(match.group(1).rstrip())
                if line in labels:
                    flush_pending()
                    current_label=line
                    continue
                if line.endswith(('：',':')) and len(line)<30:
                    flush_pending()
                    variant=line.rstrip('：:')
                    current_label=variant
                    continue
                pair=re.match(r'^[-]?\s*([^：:]{2,24})[：:]\s*(.+)$',line)
                if pair and not excluded.search(pair.group(1)):
                    flush_pending()
                    label,value=pair.groups()
                    candidates.append((variant+' / '+label if variant else label,value))
                elif len(line)<1800 and not excluded.search(section):
                    pending.append((line,start,end))
            flush_pending()
        section_facts=[]
        for label,value in candidates:
            if value not in quote or excluded.search(label):
                continue
            selected_notes=[]
            for note in notes:
                # Specific screen-version notes apply to screen type; typical-value
                # notes apply to the explicitly named metrics. Other notes stay intact.
                if '柔光' in note and not ('柔光' in value or '类型' in label):
                    continue
                if '亮度、色域、对比度、可视角度' in note and not any(x in label for x in ['亮度','色域','对比度','可视角度']):
                    continue
                selected_notes.append(note)
            # Keep lexical qualifiers from the value itself without inventing facts.
            for qualifier in ['约','典型值','额定容量','最高','最大','仅']:
                if qualifier in value and qualifier not in selected_notes:
                    selected_notes.append(qualifier)
            fid='F'+hashlib.sha256((source_id+section+label+value).encode()).hexdigest()[:14]
            section_facts.append(Fact(id=fid,product=product,label=section+' / '+label if section!=label else section,
                value=value,quote=quote,source_id=source_id,qualifiers=selected_notes,
                system=value if section=='操作系统' else ''))
        if section_facts:
            # Within dimensions prefer weight, rather than consuming the budget on length.
            def field_priority(f):
                field=f.label.split('/')[-1].strip()
                return (field not in ('重量','容量'),field!='尺寸')
            section_facts.sort(key=field_priority)
            buckets.append((section,section_facts))
    # Round-robin coverage avoids spending all eighteen facts on early screen specs.
    priority=['操作系统','特色应用','接口','电池','尺寸与重量','屏幕','处理器','内存','存储','无线连接','摄像头','音频','键盘和触控板']
    buckets.sort(key=lambda pair:priority.index(pair[0]) if pair[0] in priority else len(priority))
    output=[]
    while buckets and len(output)<max_facts:
        remaining=[]
        for section,items in buckets:
            if len(output)>=max_facts:
                break
            output.append(items[0])
            if len(items)>1:
                remaining.append((section,items[1:]))
        buckets=remaining
    # Some ima article.md exports have no headings; accept explicit field/value rows.
    if not output:
        for raw in text.splitlines():
            clean=raw.strip()
            if clean.startswith('|'):
                cells=[x.strip() for x in clean.strip('|').split('|')]
                if len(cells)<2 or any('---' in x for x in cells) or cells[0] in {'参数','项目','字段','属性'}:
                    continue
                pair=(cells[0],cells[1])
            else:
                matched=re.match(r'^[-*]?\s*([^：:]{2,24})[：:]\s*(.+)$',clean)
                pair=matched.groups() if matched else None
            if not pair or excluded.search(pair[0]) or 'http' in clean:
                continue
            label,value=pair
            fid='F'+hashlib.sha256((source_id+label+value).encode()).hexdigest()[:14]
            output.append(Fact(id=fid,product=product,label=label,value=value,quote=clean,source_id=source_id))
            if len(output)>=max_facts:
                break
    return output
