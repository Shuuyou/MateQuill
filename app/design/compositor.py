"""Compile page-specific art direction without imposing a second layout system."""
from __future__ import annotations
import hashlib,json,math
from ..poster import PosterBrief,PosterCanvas,PosterDocument,PosterLayer
from .models import DesignPlan,DesignArtboard,DesignPage,LayoutRegion,region_z
from .typography import fit_text,semantic_wrap,measure
from .font_resolver import font_provenance

def _region_effects(region):
    return {key:getattr(region,key) for key in ('rotation','shadow_color','shadow_opacity','shadow_blur','shadow_dx','shadow_dy')}


def _region_box(region,w,h):
    x,y,bw,bh=region.x*w,region.y*h,region.width*w,region.height*h
    if region.rotation:
        angle=math.radians(region.rotation)
        rw=abs(bw*math.cos(angle))+abs(bh*math.sin(angle))
        rh=abs(bw*math.sin(angle))+abs(bh*math.cos(angle))
        x+=(bw-rw)/2;y+=(bh-rh)/2;bw=rw;bh=rh
    return x,y,bw,bh


def _safe_text_expansion(region,content,size,family,weight,w,h,obstacles):
    """Solve only missing text space; never move a source subject or rewrite copy."""
    original=(region.x*w,region.y*h,region.width*w,region.height*h)
    if region.rotation:return None
    x,y,bw,bh=original
    def intersects(box,other):
        return min(box[0]+box[2],other[0]+other[2])-max(box[0],other[0])>.1 and min(box[1]+box[3],other[1]+other[3])-max(box[1],other[1])>.1
    # Intentional type/photo overlap needs pixel judgement, not a box solver
    # pretending it knows which part of a photograph is safe to cover.
    if any(intersects(original,other) for other in obstacles):return None
    right=x+bw;center=x+bw/2
    limit=right if region.align=='right' else 2*min(center,w-center) if region.align=='center' else w-x
    widths={bw,limit,max((measure(line,size,region.letter_spacing,family,weight) for line in content.split('\n')),default=0)+.05}
    for ox,oy,ow,oh in obstacles:
        if region.align=='right':widths.add(right-(ox+ow))
        elif region.align=='center':
            widths.add(2*abs(center-(ox+ow)));widths.add(2*abs(ox-center))
        else:widths.add(ox-x)
    valid=[]
    for width in widths:
        if width<bw-.01 or width>limit+.01:continue
        lines=semantic_wrap(content,width,size,region.letter_spacing,family,weight)
        measured=max((measure(line,size,region.letter_spacing,family,weight) for line in lines),default=0)
        height=max(bh,len(lines)*size*region.line_height+.05)
        left=right-width if region.align=='right' else center-width/2 if region.align=='center' else x
        box=(left,y,width,height)
        if measured>width+.01 or left<-.01 or left+width>w+.01 or y+height>h+.01:continue
        if any(intersects(box,other) for other in obstacles):continue
        cost=((width-bw)/w+(height-bh)/h,width*height)
        valid.append((cost,box,'\n'.join(lines)))
    if not valid:return None
    _,box,text=min(valid,key=lambda row:row[0])
    return box,text


def functional_text_occlusion_checks(document):
    """Paint-order guard shared by compilation and standalone document audit."""
    layers=(document.layers if isinstance(document,PosterDocument) else document if isinstance(document,list)
        else PosterDocument.model_validate(document).layers)
    issues=[]
    for text in (layer for layer in layers if layer.type=='text' and layer.visible and layer.text):
        for picture in (layer for layer in layers if layer.type=='image' and layer.visible and layer.role in ('product','photograph')):
            if text.z>picture.z:continue
            ow=min(text.x+text.width,picture.x+picture.width)-max(text.x,picture.x)
            oh=min(text.y+text.height,picture.y+picture.height)-max(text.y,picture.y)
            if ow>.1 and oh>.1 and (text.role!='headline' or picture.role=='photograph'):
                issues.append({'severity':'error','category':'functional_text_occlusion',
                    'message':f'{text.role or "functional text"} is painted behind {picture.role}; retain the approved wording and revise composition',
                    'text_layer_id':text.id,'image_layer_id':picture.id})
    return issues


def apply_full_frame_visual(document,asset_id:str,metadata:dict)->PosterDocument:
    """Use a completed visual as the artwork, not as a backdrop for generic type.

    Source-native layers should remain in the job's prior version for editing;
    this representation accurately declares a single generated raster delivery.
    The service owns authorization, actual visual review, and product verification.
    """
    if not asset_id: raise ValueError('generated_design_asset_missing')
    doc=(document if isinstance(document,PosterDocument) else PosterDocument.model_validate(document)).model_copy(deep=True)
    doc.layers=[PosterLayer(id=f'{doc.id}-visual',type='image',name='完整视觉作品',x=0,y=0,
        width=doc.width,height=doc.height,z=0,asset_id=asset_id,role='generated_design',locked=True)]
    doc.assets={asset_id:dict(metadata)}
    doc.status='draft'
    return doc


def compose_design(task,record,plan,assets:dict)->list[DesignArtboard]:
    plan=plan if isinstance(plan,DesignPlan) else DesignPlan.model_validate(plan)
    task=task.model_dump() if hasattr(task,'model_dump') else dict(task or {})
    platform=plan.brief.platform;w,h=platform.width,platform.height
    selected=next(c for c in plan.concepts if c.id==plan.selected_concept_id)
    texts=plan.brief.texts;boards=[]
    if selected.pages:
        pages=selected.pages[:platform.suggested_pages]
    else:
        # Backward compatibility for saved plans: reuse their authored geometry,
        # never silently replace later pages with half-image/paragraph templates.
        count=max(1,len(selected.page_asset_ids))
        roles=platform.image_sequence[:min(platform.suggested_pages,count)]
        pages=[DesignPage(id=f'legacy-{index}',role=role,composition=selected.composition,
            text_ids=selected.text_ids,render_mode='native',visual_brief=selected.visual_thesis)
            for index,role in enumerate(roles)]
    for index,page in enumerate(pages):
        concept=selected
        source_ids=list(page.text_ids)+[region.text_id for region in page.composition if region.text_id]
        layers=[];metrics={};issues=[]
        ids=[key for key in (page.text_ids or concept.text_ids) if key in texts]
        is_legacy=page.id.startswith('legacy-')
        headline_id=('title' if 'title' in ids else next(iter(ids),'')) if index==0 else ''
        body_ids=[key for key in ids if key not in ('title',headline_id)]
        body_id=body_ids[min(index,len(body_ids)-1)] if body_ids else ''
        if is_legacy and body_id and len(texts[body_id]['text'])>90: body_id=''
        if page.role=='photo':headline_id='';body_id=''
        if index<len(concept.page_asset_ids):
            aid=concept.page_asset_ids[index]
        elif page.asset_id: aid=page.asset_id
        else:
            candidates=[key for key in concept.asset_ids if key in assets]
            candidates+= [key for key in plan.brief.asset_ids if key in assets and key not in candidates]
            aid=candidates[index%len(candidates)] if candidates else next(iter(assets),'')
        if aid and aid not in assets: raise ValueError('planned_design_asset_unavailable')
        referenced_ids=set(source_ids)|set(ids)
        execution={
            'version':'native-execution-v3-safe-text-fit','concept_id':concept.id,'page_id':page.id,'page_index':index,
            'page':page.model_dump(mode='json'),'resolved_primary_asset_id':aid,
            'copy':{key:texts[key] for key in sorted(referenced_ids) if key in texts},
            'palette':{key:getattr(concept,key) for key in ('background','foreground','accent')},
            'font_role':concept.font_role,'visual_thesis':concept.visual_thesis,
            'art_direction':concept.art_direction.model_dump(mode='json'),
            'canvas':[w,h]}
        execution_digest=hashlib.sha256(json.dumps(execution,ensure_ascii=False,sort_keys=True,separators=(',',':')).encode()).hexdigest()
        def stable(label):
            return hashlib.sha256(f'{plan.provenance.get("source_digest","")}:{execution_digest}:{label}'.encode()).hexdigest()[:16]
        expanded_text_boxes=[]
        def text_layer(region,content,key,semantic_role,default_size):
            if not content:return
            x,y,bw,bh=region.x*w,region.y*h,region.width*w,region.height*h
            family=region.font_role or (concept.font_role if semantic_role=='headline' else 'sans')
            weight=region.font_weight or (700 if semantic_role=='headline' else 400)
            maximum=region.font_scale*w if region.font_scale is not None else default_size
            minimum=math.ceil(w*(.05 if semantic_role=='headline' else .024 if semantic_role in ('brand','kicker') else .033))
            minimum=max(20,minimum)
            text,fontsize,fit=fit_text(content,bw,bh,min(max(maximum,minimum),1600),minimum=minimum,line_height=region.line_height,font_family=family,font_weight=weight)
            if region.letter_spacing:
                while True:
                    lines=semantic_wrap(content,bw,fontsize,region.letter_spacing,family,weight)
                    fit=max((measure(line,fontsize,region.letter_spacing,family,weight) for line in lines),default=0)<=bw and len(lines)*fontsize*region.line_height<=bh
                    text='\n'.join(lines)
                    if fit or fontsize<=minimum:break
                    fontsize-=1
            adjustment=None
            if not fit:
                obstacles=[_region_box(other,w,h) for other in page.composition
                    if other is not region and other.role not in ('shape','rule')]
                obstacles+=expanded_text_boxes
                expansion=_safe_text_expansion(region,content,fontsize,family,weight,w,h,obstacles)
                if expansion:
                    previous_box=[x,y,bw,bh]
                    (x,y,bw,bh),text=expansion;fit=True
                    adjustment={'kind':'minimum_safe_blank_space_expansion','original_box':previous_box,
                        'resolved_box':[x,y,bw,bh],'font_size_preserved':fontsize,'source_copy_preserved':True}
            if semantic_role=='headline' and '\n' not in content:
                lines=text.splitlines()
                widths=[measure(line,fontsize,region.letter_spacing,family,weight) for line in lines]
                if len(lines)>1 and widths[-1]<max(widths)*.35:
                    # Do not strand one or two final characters merely because
                    # the maximum headline size fits vertically. Reflow using
                    # the same exact approved copy and actual glyph metrics.
                    for candidate in range(int(fontsize)-1,minimum-1,-1):
                        candidate_lines=semantic_wrap(content,bw,candidate,region.letter_spacing,family,weight)
                        candidate_widths=[measure(line,candidate,region.letter_spacing,family,weight) for line in candidate_lines]
                        candidate_fit=max(candidate_widths,default=0)<=bw and len(candidate_lines)*candidate*region.line_height<=bh
                        if candidate_fit and (len(candidate_lines)<len(lines) or candidate_widths[-1]>=max(candidate_widths)*.35):
                            fontsize=candidate;text='\n'.join(candidate_lines);fit=True
                            break
            source=texts.get(key,{})
            layer_id=stable(f'{semantic_role}-{len(layers)}')
            layers.append(PosterLayer(id=layer_id,type='text',name=semantic_role,x=x,y=y,width=bw,height=bh,z=region_z(region),
                text=text,font_size=fontsize,font_weight=weight,color=region.color or concept.foreground,
                font_family=family,role=semantic_role,align=region.align,line_height=region.line_height,
                letter_spacing=region.letter_spacing,fact_ids=source.get('fact_ids',[]),source_ids=source.get('source_ids',[]),
                **_region_effects(region)))
            metrics[layer_id]={**font_provenance(content,family,weight),'measured':True,'fits':fit,'source_text_id':key}
            if adjustment:metrics[layer_id]['layout_adjustment']=adjustment
            expanded_text_boxes.append((x,y,bw,bh))
            if key:source_ids.append(key)
            if not fit:issues.append({'severity':'error','category':'overflow','message':f'{semantic_role} needs a larger authored region or a shorter approved text'})
        for region in page.composition:
            if region.role in ('shape','rule'):
                layers.append(PosterLayer(id=stable(f'decoration-{len(layers)}'),type='shape',name='构图元素',
                    x=region.x*w,y=region.y*h,width=region.width*w,height=region.height*h,z=region_z(region),
                    geometry='line' if region.role=='rule' else region.geometry,
                    fill=region.fill or concept.accent,
                    stroke=region.stroke or (region.fill if region.fill and region.fill!='none' else concept.accent),
                    stroke_width=region.stroke_width or (2 if region.role=='rule' else 0),
                    path_d=region.path_d,fill_style=region.fill_style,gradient_end=region.gradient_end,
                    opacity=region.opacity,role='design_shape',**_region_effects(region)))
                continue
            if region.role=='image':
                image_id=region.asset_id or aid
                if image_id:
                    if image_id not in assets:raise ValueError('planned_design_asset_unavailable')
                    meta=assets[image_id];visual=meta.get('visual_role','product')
                    is_photo=visual in ('store_photo','photo','scene','lifestyle','real_scene','user_photo')
                    layers.append(PosterLayer(id=stable(f'image-{len(layers)}'),type='image',name='授权素材',x=region.x*w,y=region.y*h,
                        width=region.width*w,height=region.height*h,z=region_z(region),asset_id=image_id,locked=True,
                        role='photograph' if is_photo else 'product',source_ids=[str(meta['source_id'])] if meta.get('source_id') else [],
                        **_region_effects(region)))
                else:issues.append({'severity':'warning','category':'asset','message':'No supplied image; native text design retained'})
                continue
            key=region.text_id
            if key:
                if key not in texts:raise ValueError('unknown_design_text_reference')
                text_layer(region,texts[key]['text'],key,region.role,w*.09 if region.role=='headline' else w*.036)
            elif region.role=='headline' and headline_id:
                text_layer(region,texts[headline_id]['text'],headline_id,'headline',w*.10);headline_id=''
            elif region.role=='brand' and plan.brief.product:
                text_layer(region,plan.brief.product,'','brand',min(w*.024,36))
            elif region.role in ('body','caption','kicker') and body_id:
                text_layer(region,texts[body_id]['text'],body_id,region.role,w*.034);body_id=''
        no_type=any(word in plan.brief.instruction for word in ('不要文字','不加字','无字','纯照片'))
        if not layers and texts and not no_type:
            key='title' if 'title' in texts else next(iter(texts))
            region=LayoutRegion(role='headline',x=.08,y=.20,width=.84,height=.55)
            text_layer(region,texts[key]['text'],key,'headline',w*.10)
        brief=PosterBrief(size_preset='custom',width=w,height=h,headline=texts.get('title',{}).get('text',''),
            brand_name=plan.brief.product,purpose=task.get('goal') or '门店内容',background_color=concept.background,accent_color=concept.accent)
        # Old plans or mutated model instances can bypass region validation.
        # Judge actual compiled roles and paint order, not an overlap label.
        issues.extend(functional_text_occlusion_checks(layers))
        doc=PosterDocument(id=stable('document'),width=w,height=h,canvas=PosterCanvas(width=w,height=h,background=concept.background),
            brief=brief,layers=layers,assets={key:dict(value) for key,value in assets.items() if key in {layer.asset_id for layer in layers if layer.asset_id}},
            source_run_id=plan.brief.source_run_id,audit=issues,status='blocked' if any(item['severity']=='error' for item in issues) else 'draft')
        boards.append(DesignArtboard(id=stable('board'),role=page.role,title=page.title or concept.name,concept_id=concept.id,
            document=doc,typography=metrics,source_text_ids=list(dict.fromkeys(source_ids)),visual_brief=page.visual_brief or concept.visual_thesis,
            render_mode=page.render_mode,page_id=page.id))
    return boards
