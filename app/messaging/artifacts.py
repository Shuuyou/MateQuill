"""Resolve only images actually embedded in an authorized, finished artifact."""
import asyncio
from pathlib import Path
from fastapi import HTTPException
from app.tenancy import authorize_resource


def artifact_text(reference,audience='private'):
    from app.gtm_agent.repository import artifact_reference
    artifact_reference(reference['kind'],reference['id'],audience=audience)
    if reference['kind']!='run':
        return reference.get('title','海报设计')
    content=authorize_resource('run',reference['id'])['body']
    parts=[content.get('title',''),content.get('intro','')]
    for block in content.get('blocks',[]):
        if isinstance(block,dict):
            parts.extend([block.get('heading',''),block.get('text','') or block.get('body','')])
    parts.append(content.get('cta',''))
    if content.get('tags'):
        parts.append(' '.join('#'+str(tag).lstrip('#') for tag in content['tags']))
    rendered='\n\n'.join(str(part).strip() for part in parts if part)
    return rendered.encode('utf-8')[:11000].decode('utf-8',errors='ignore')


async def artifact_images(reference,audience='private'):
    from app.gtm_agent.repository import artifact_reference
    from app.media_intake import normalize_photo
    artifact_reference(reference['kind'],reference['id'],audience=audience)
    if reference['kind']=='design':
        from app.design_service import load_design, artboard, render_document
        document=load_design(reference['id'])
        result=[]
        for board in document.get('artboards',[])[:10]:
            raw=await render_document(artboard(reference['id'],board['id']))
            result.append((await asyncio.to_thread(normalize_photo,raw))[0])
        return result
    if reference['kind']=='poster':
        from app.poster_routes import export_poster
        response=await export_poster(reference['id'],format='png')
        raw=response.body
        return [(await asyncio.to_thread(normalize_photo,raw))[0]]
    meta=authorize_resource('run',reference['id'])
    snapshot=meta['body']
    selected=[item.get('asset_id') for item in snapshot.get('images',[]) if isinstance(item,dict)]
    selected.extend(aid for card in (snapshot.get('creative_plan') or {}).get('cards',[]) for aid in card.get('asset_ids',[]))
    from app.parent_assets import read_parent_asset
    result=[]
    for asset_id in dict.fromkeys(selected):
        if not asset_id or len(result)>=10:
            continue
        response=await read_parent_asset(meta,snapshot,asset_id)
        raw=getattr(response,'body',None)
        if raw is None and getattr(response,'path',None):
            path=Path(response.path)
            if path.stat().st_size>12*1024*1024:
                raise ValueError('artifact_image_too_large')
            raw=await asyncio.to_thread(path.read_bytes)
        if not isinstance(raw,bytes):
            raise ValueError('artifact_image_unavailable')
        normalized=(await asyncio.to_thread(normalize_photo,raw))[0]
        result.append(normalized)
    return result
