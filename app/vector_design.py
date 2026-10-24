"""Typography-led, editable artboards with protected product photography.

This is a deterministic design system, not a claim to reproduce a commercial
design agent. The three directions have separate spatial rhythms. Every word
remains an editable text layer and every motif remains a vector primitive.
"""
from __future__ import annotations

from .poster import (PosterBrief, PosterCanvas, PosterDocument, PosterLayer,
                     _data, _fit, _units, audit_document, build_poster)
from .art_direction import plan_art_direction


def compose_vector_poster(task, result, brief: PosterBrief | dict) -> PosterDocument:
    brief = brief if isinstance(brief, PosterBrief) else PosterBrief.model_validate(brief)
    source = _data(result)
    task_data = _data(task)
    # Reuse provenance-aware image selection, not its composition or generated
    # background. The reference product itself is never reimagined by a model.
    safe_brief = brief.model_copy(update={'background_asset_id': '', 'background_mode': 'solid'})
    seed = build_poster(task, result, safe_brief)
    product = next((item for item in seed.layers if item.role == 'product'), None)
    conditions = next((item for item in seed.layers if item.role == 'disclosure'), None)
    w, h = brief.width, brief.height
    landscape = w / h > 1.25
    square = .90 < w / h <= 1.25
    unit = min(w / 1080, h / 1440)
    direction = brief.direction
    plan = plan_art_direction(task, result, brief=brief)
    palette = plan['palette']
    bg, fg, accent, muted = (palette[key] for key in ('background','foreground','accent','muted'))
    # Default green was inherited from the application shell, not a brand
    # requirement. Explicitly chosen colors still take precedence.
    if brief.accent_color != '#17654D':
        accent = brief.accent_color
    if brief.background_color != '#F3F0E8':
        bg = brief.background_color
    layers = []

    def add(kind, name, x, y, width, height, **values):
        values = {key: value for key, value in values.items() if key in PosterLayer.model_fields}
        item = PosterLayer(id=f'vector-{len(layers) + 1}', type=kind, name=name,
                           x=x*w, y=y*h, width=width*w, height=height*h,
                           z=len(layers), **values)
        if item.role=='headline' and '\n' in item.text:
            # A planned phrase must remain on one line; fitting only total
            # block height otherwise strands its last character on a third row.
            limits=[(item.width-max(0,len(line)-1)*item.letter_spacing)/sum(_units(c) for c in line)
                    for line in item.text.split('\n') if line]
            if limits:
                item.font_size=max(18,min(item.font_size,*limits))
        if kind == 'text':
            _fit(item, minimum=10 if item.role == 'watermark' else 14 if item.role == 'disclosure' else 18)
        layers.append(item)
        return item

    def text(name, content, box, size, *, color=fg, weight=400, role='', family=None, **extra):
        family = family or plan['fonts']['headline' if role=='headline' else 'body']
        return add('text', name, *box, text=content, font_size=max(10, size*unit),
                   color=color, font_weight=weight, role=role, font_family=family, **extra)

    def shape(name, box, fill=accent, **extra):
        return add('shape', name, *box, fill=fill, radius=0, **extra)

    def rule(name, x, y, length, fill=fg):
        return shape(name, (x,y,length,max(1/h,.001)), fill, role='vector_rule')

    def photograph(box):
        if product:
            return add('image', '真实产品图 · 独立锁定', *box, role='product', locked=True,
                       asset_id=product.asset_id, source_ids=product.source_ids)
        return shape('等待准确型号的真实产品素材', box, '#D9DBD8', role='missing_product')

    approved_copy = source.get('strategy',{}).get('deliverable') == 'single_screen_poster'
    headline = brief.headline or (source.get('title') if approved_copy else '') or '\n'.join(plan['headline_lines'])
    if not brief.headline and headline.replace('\n','')==''.join(plan['headline_lines']):
        headline='\n'.join(plan['headline_lines'])
    if not brief.headline and '\n' not in headline and len(headline)>6:
        # Balanced phrase breaks, never orphan the final Chinese character.
        split=next((headline.index(mark)+1 for mark in ('，','。','、') if mark in headline[:-2]),None)
        if split is None:
            positions=[headline.index(word) for word in ('写成','带上','留给','到','为','让')
                       if word in headline and 2<=headline.index(word)<=len(headline)-2]
            split=min(positions,key=lambda pos:abs(pos-len(headline)/2)) if positions else len(headline)//2
        headline=headline[:split].rstrip('，。')+'\n'+headline[split:]
    subtitle = brief.subtitle or source.get('intro') or ''
    brand = brief.brand_name or task_data.get('product', '')
    cta = brief.cta or source.get('cta') or '到店体验你的真实任务'
    blocks = source.get('blocks', [])[:3]
    audience = str(task_data.get('audience', ''))
    # The spine is an editorial section title, never the user's full command.
    section_title = ('学习日常' if any(w in audience for w in ('学生','校园','家长')) else
                     '创作日常' if any(w in audience for w in ('创作','设计','摄影')) else
                     '工作日常' if any(w in audience for w in ('商务','职场','办公','出差')) else '灵感日常')

    def benefits(boxes, size=23, color=fg):
        for number, (block, box) in enumerate(zip(blocks, boxes), 1):
            text(f'产品依据 {number}', block.get('text', ''), box, size,
                 color=color, role='benefit', fact_ids=block.get('fact_ids', []))

    # Stable bottom strip is reserved for complete commercial qualifications;
    # it is never replaced by decorative lorem ipsum or silently truncated.
    if direction == 'minimal':
        text('品牌标识', brand, (.065,.038,.69,.045), 23, weight=600, role='brand')
        text('场景栏目', section_title, (.77,.041,.17,.03), 14,
             color=muted, family='mono', align='right', role='design_annotation')
        rule('页眉细线', .065,.105,.87)
        if landscape:
            text('非对称主标题', headline, (.065,.17,.45,.30), 100, weight=800, role='headline', letter_spacing=-2)
            text('场景叙事', subtitle, (.065,.50,.37,.10), 25, color=muted, role='subtitle')
            photograph((.52,.18,.42,.53))
            benefits([(.065,.68+i*.065,.40,.055) for i in range(3)], 22)
            text('行动文字', cta, (.55,.78,.38,.07), 27, weight=600, role='cta')
        else:
            text('非对称主标题', headline, (.065,.15,.79,.235), 110, weight=800, role='headline', letter_spacing=-3)
            text('场景叙事', subtitle, (.065,.40,.80,.065), 26, color=muted, role='subtitle')
            # A vertical blue rule balances an intentionally offset photograph.
            shape('视觉基准色条', (.065,.505,.012,.24), accent, role='vector_motif')
            photograph((.24,.49,.695,.275))
            rule('信息分隔线', .065,.792,.87, '#BCBFBD')
            benefits([(.065+i*.30,.812,.27,.065) for i in range(3)], 23)
            text('行动文字', cta, (.065,.89,.87,.045), 27, weight=600, role='cta')
    elif direction == 'editorial':
        text('编辑式品牌', brand, (.065,.035,.78,.04), 22, weight=600, role='brand', family='serif')
        rule('编辑页眉线', .065,.092,.87, accent)
        if landscape:
            shape('编辑侧栏', (.065,.13,.24,.73), accent, role='vector_motif')
            text('场景索引', section_title, (.085,.17,.19,.22), 68,
                 color='#FFFFFF', family='serif', role='design_annotation')
            benefits([(.085,.45+i*.12,.18,.105) for i in range(3)], 22, '#FFFFFF')
            text('编辑主标题', headline, (.36,.14,.57,.25), 92, family='serif', weight=700, role='headline')
            text('编辑导语', subtitle, (.36,.42,.51,.075), 25, color=muted, role='subtitle')
            photograph((.46,.52,.47,.28))
            text('行动文字', cta, (.36,.82,.57,.06), 25, role='cta', weight=600)
        else:
            # The left column is a publication spine, not a rounded UI card.
            shape('编辑书脊', (.065,.14,.19,.73), accent, role='vector_motif')
            text('场景索引', section_title, (.084,.17,.15,.14), 48,
                 color='#FFFFFF', family='serif', role='design_annotation')
            benefits([(.084,.365+i*.155,.15,.13) for i in range(3)], 22, '#FFFFFF')
            text('编辑主标题', headline, (.31,.14,.625,.24), 96, family='serif', weight=700, role='headline')
            text('编辑导语', subtitle, (.31,.405,.625,.075), 25, color=muted, role='subtitle')
            photograph((.31,.51,.625,.275))
            rule('编辑落款线', .31,.815,.625, accent)
            text('行动文字', cta, (.31,.835,.625,.055), 25, role='cta', weight=600)
    else:
        text('品牌标识', brand, (.065,.035,.78,.045), 23, weight=600, role='brand')
        # Solid geometry establishes the composition before any raster exists.
        if landscape:
            shape('强调色舞台', (.50,.12,.50,.64), accent, role='vector_motif')
            shape('圆形几何', (.68,.16,.25,.45), '#FFFFFF', opacity=.28, geometry='ellipse', role='vector_motif')
            text('宣言主标题', headline, (.065,.19,.39,.36), 112, weight=800, role='headline', letter_spacing=-2)
            text('场景说明', subtitle, (.065,.60,.36,.11), 25, color=muted, role='subtitle')
            photograph((.53,.245,.42,.40))
            benefits([(.53+i*.14,.79,.125,.09) for i in range(3)], 22)
            text('行动文字', cta, (.065,.80,.40,.07), 27, weight=700, role='cta')
        else:
            shape('纵向强调色舞台', (.515,.135,.485,.77), accent, role='vector_motif')
            shape('圆形几何', (.62,.19,.32,.26), '#FFFFFF', opacity=.28, geometry='ellipse', role='vector_motif')
            text('宣言主标题', headline, (.065,.18,.43,.37), 90, weight=800, role='headline', letter_spacing=-2)
            text('场景说明', subtitle, (.065,.59,.39,.14), 26, color=muted, role='subtitle')
            photograph((.54,.37,.42,.30))
            benefits([(.54,.70+i*.067,.39,.059) for i in range(3)], 22, '#172322')
            text('行动文字', cta, (.065,.795,.39,.09), 27, weight=700, role='cta')
        rule('底部结构线', .065,.914,.87, '#515659')

    if conditions:
        text('事实限定与适用条件', conditions.text, (.065,.941,.87,.033), 16,
             color=muted, role='disclosure', locked=True, fact_ids=conditions.fact_ids)
    text('草稿状态水印', '' if source.get('status') == 'ready' else '设计草稿 · 产品素材与文案需复核后发布',
         (.065,.979,.87,.018), 12, color=muted, role='watermark', locked=True, align='right')
    document = PosterDocument(width=w, height=h,
        canvas=PosterCanvas(width=w,height=h,background=bg), brief=brief,
        layers=layers, assets=seed.assets, source_run_id=seed.source_run_id)
    # Keep content/provenance findings, but run spatial checks on the new artboard.
    document.audit = [item for item in seed.audit if item.get('category') not in
                      ('bounds','overflow','overlap','legibility','background')]
    document.audit.extend(audit_document(document))
    document.status = 'blocked' if any(item.get('severity') == 'error' for item in document.audit) else 'draft'
    return document
