"""Render a commissioned craft proof through the actual production compositor.

This is explicitly directed art work, not a fabricated autonomous API run or a
claim that a third-party visual judge approved it. Local refinement orders keep
the actual wording, SKU/source bytes and all unrelated regions immutable.
"""
from __future__ import annotations
import argparse
import asyncio
from copy import deepcopy
import hashlib
import json
import shutil
from pathlib import Path
import sys

ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from app.design.models import DesignBrief,DesignConcept,DesignPage,DesignPlan,LayoutRegion
from app.design.connectors import get_platform_connector
from app.design.compositor import compose_design
from app.design.renderer import render_design_svg
from app.design.source_integrity import native_source_proof
from app.design.craft import build_craft_contract,check_craft_contract
from app.design.craft_controls import apply_craft_controls
from app.design_service import render_document


def save(path,value):
    path.write_text(json.dumps(value,ensure_ascii=False,indent=2),encoding='utf-8')


async def render_case(plan,assets,output,stem):
    board=compose_design({'product':plan.brief.product},{},plan,assets)[0]
    svg=render_design_svg(board.document)
    png=await render_document(board.document)
    (output/(stem+'.png')).write_bytes(png);(output/(stem+'.svg')).write_text(svg,encoding='utf-8')
    save(output/(stem+'.plan.json'),plan.model_dump(mode='json'))
    save(output/(stem+'.document.json'),board.document.model_dump(mode='json'))
    return {'png_sha256':hashlib.sha256(png).hexdigest(),
            'source_proof':native_source_proof(board.document,assets),
            'font_metrics':board.typography,
            'structure':check_craft_contract(plan.provenance['craft_contract'],plan,board.document)}


async def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--stage',choices=['initial','refined','optical'],default='initial');p.add_argument('--refresh-optical',action='store_true')
    args=p.parse_args();source=ROOT/'runtime/craft-quality-20261024';out=ROOT/'output/craft-proof-20261024';out.mkdir(exist_ok=True,parents=True)
    assets=json.loads((source/'prepared-assets.json').read_text(encoding='utf-8'))
    observed=json.loads((source/'material-observations.json').read_text(encoding='utf-8'))
    def subject(index):
        digest=observed[index]['sha256']
        return next((key,row) for key,row in assets.items() if row.get('catalog_sha256')==digest)
    front,frontmeta=subject(3);rear,rearmeta=subject(4)
    assets={front:frontmeta,rear:rearmeta}
    W,H=1080,1440
    def box(x,y,w,h):return {'x':x/W,'y':y/H,'width':w/W,'height':h/H}
    text={'model':{'text':'HUAWEI Mate 80 Pro','fact_ids':[],'source_ids':['vmall-source']},
          'headline':{'text':'目光，\n总会多绕一圈。','fact_ids':[],'source_ids':[]},
          'color':{'text':'雪域白','fact_ids':[],'source_ids':['vmall-source']}}
    def image(identifier,aid,meta,x,y,height,z):
        width=height*meta['width']/meta['height']
        return LayoutRegion(id=identifier,role='image',asset_id=aid,z=z,overlap_intent='layered_assets',
            shadow_color='#303844',shadow_opacity=.16,shadow_blur=22,shadow_dy=24,**box(x,y,width,height))
    regions=[LayoutRegion(id='model',role='brand',text_id='model',font_fit='authored',font_scale=38/W,font_weight=500,letter_spacing=-.3,**box(76,70,928,60)),
        LayoutRegion(id='headline',role='headline',text_id='headline',font_fit='authored',font_scale=72/W,font_weight=500,
                     letter_spacing=0,line_height=1.15,**box(76,165,920,190)),
        image('front',front,frontmeta,200,400,900,10),
        image('rear',rear,rearmeta,555,400,900,12),
        LayoutRegion(id='color',role='caption',text_id='color',font_fit='authored',font_scale=23/W,font_weight=400,
                     letter_spacing=2,align='right',**box(800,1350,204,42))]
    brief=DesignBrief(platform=get_platform_connector('poster'),product='HUAWEI Mate 80 Pro',
        publisher_kind='store',scene='以雪域白手机可见双环外观为单图主视觉，不作性能承诺。',
        instruction='产品主视觉；保留准确物体、视角和文字，精修空间关系、字距与接触影。',texts=text,asset_ids=list(assets))
    contract=build_craft_contract(brief,{'craft_requirements':{'communication_proposition':'视线在真实双环外观间逡巡，与“多绕一圈”形成可见双读。',
        'visual_bridge':'背面双环是焦点，正面作为配角；不遮下环、不加磁力或轨道特效。','required_view':'rear','primary_region_id':'rear'}},assets)
    plan=DesignPlan(brief=brief,concepts=[DesignConcept(id='rings',name='双环与视线',
        visual_thesis='让白色背面的上下双环成为注意中心，正面承接前后视角。',
        composition=regions,pages=[DesignPage(id='cover',role='cover',title='双环与视线',composition=regions)],
        production_method='native_assembly',asset_ids=list(assets),background='#F5F7FA',foreground='#20272E',accent='#8B97A4')],
        selected_concept_id='rings',provenance={'planner':'codex_directed_craft_proof','source_digest':'mate80-pro-exact-sku-white',
            'craft_contract':contract,'copy_origin':'independent creative peer review; no claim of production API approval'})
    if args.stage=='optical':
        parent_path=out/'02-refined.plan.json'
        if not parent_path.exists():raise RuntimeError('The actual refined parent plan is required')
        prior_optical_sha=None
        if (out/'03-optical.png').exists():
            if not args.refresh_optical:raise RuntimeError('Preserve the existing optical proof; use an explicit archived refresh')
            archive=out/'03-before-font-fit-authored'
            archive.mkdir()
            for previous in out.glob('03-optical*'):
                if previous.is_file():shutil.copyfile(previous,archive/previous.name)
            prior_optical_sha=hashlib.sha256((out/'03-optical.png').read_bytes()).hexdigest()
        parent=DesignPlan.model_validate_json(parent_path.read_text(encoding='utf-8'))
        old_contract=deepcopy(parent.provenance['craft_contract'])
        # A new, fully built contract scopes rear-view requirements to the hero.
        # Preserve historical 01/02 contracts instead of mutating their IDs.
        contract=build_craft_contract(parent.brief,{'craft_requirements':{
            'communication_proposition':'视线在真实双环外观间逡巡，与“多绕一圈”形成可见双读。',
            'visual_bridge':'背面双环是焦点，正面作为配角；不遮下环、不加磁力或轨道特效。',
            'required_view':'rear','primary_region_id':'rear'}},assets)
        if old_contract['locks']!=contract['locks']:raise RuntimeError('Contract migration cannot change source or copy locks')
        parent_data=parent.model_dump(mode='json')
        plan=parent.model_copy(deep=True)
        plan.provenance={**plan.provenance,'craft_contract':contract}
        migration={'from_plan':'02-refined.plan.json',
            'from_plan_sha256':hashlib.sha256(parent_path.read_bytes()).hexdigest(),
            'from_contract_id':old_contract['id'],'to_contract_id':contract['id'],
            'required_view':'rear','primary_region_id':'rear','source_and_copy_locks_unchanged':True,
            'reason':'Build a fresh source-bound hero-scoped contract; retain original 01/02 historical records.'}
        selected=next(c for c in plan.concepts if c.id==plan.selected_concept_id)
        front_region=next(r for r in selected.pages[0].composition if r.id=='front')
        front_h=front_region.height*H*.96
        rear_region=next(r for r in selected.pages[0].composition if r.id=='rear')
        updates=[('model','降低产品行字号，让型号与主题形成更轻的层级。',{'font_scale':33/W,'font_fit':'authored'}),
            ('headline','同文案的光学排版：小幅收字，开放行距，给背面顶部留出约70px墨迹间隔。',
             {'font_scale':67/W,'font_fit':'authored','line_height':1.18,'letter_spacing':-.6,'y':145/H}),
            ('front','前机等比例缩4%，保留左侧重叠关系，底部与背面共线1330px；源像素与不透明度不变。',
             {**box(front_region.x*W,1330-front_h,front_h*frontmeta['width']/frontmeta['height'],front_h),
              'contact_shadow':{**front_region.contact_shadow.model_dump(),'center_y':1.0015}}),
            ('rear','主背面位置与尺寸不变，只把接触影移至底缘约1.4px。',
             {'contact_shadow':{**rear_region.contact_shadow.model_dump(),'center_y':1.0015}}),
            ('color','颜色脚注保留字号与右对齐，向上16px恢复底部呼吸。',{'y':1334/H,'font_fit':'authored','font_scale':23/W})]
        orders=[]
        for region,goal,values in updates:
            plan,order=apply_craft_controls(plan,contract,page_id='cover',region_id=region,goal=goal,values=values)
            orders.append(order)
        report=await render_case(plan,assets,out,'03-optical')
        if plan.brief.texts!=parent.brief.texts or plan.brief.asset_ids!=parent.brief.asset_ids:
            raise RuntimeError('Optical revision changed approved copy or source references')
        report.update(refinement_orders=orders,contract_migration=migration,
            schema_renderer_correction={'change':'Explicit font_fit authored restores commissioned exact sizes under new adaptive default; no historical order was rewritten.',
                'previous_optical_png_sha256':prior_optical_sha, 'current_png_sha256':report['png_sha256'],
                'png_equal_previous':report['png_sha256']==prior_optical_sha if prior_optical_sha else None,
                'archived_previous':'03-before-font-fit-authored' if prior_optical_sha else None,
                'explicit_sizes_px':{'model':33,'headline':67,'color':23}},
            status='directed_craft_proof',production_api_approval=False,api_image_generation_calls=0,
            source_and_copy_changes=False,parent_plan_sha256=hashlib.sha256(parent_path.read_bytes()).hexdigest())
        save(out/'03-optical-report.json',report)
        print(json.dumps({'stage':'optical','path':str(out/'03-optical.png'),'issues':report['structure']},ensure_ascii=True))
        return
    if args.stage=='initial':
        report=await render_case(plan,assets,out,'01-initial')
        save(out/'initial-report.json',report)
        print(json.dumps({'stage':'initial','path':str(out/'01-initial.png'),'issues':report['structure']},ensure_ascii=True));return
    initial=out/'01-initial.plan.json'
    if not initial.exists():raise RuntimeError('Inspect the real initial render before authoring refinement')
    plan=DesignPlan.model_validate_json(initial.read_text(encoding='utf-8'));contract=plan.provenance['craft_contract'];orders=[]
    # Each step is a single-region order. Every other region, source and exact
    # public string is locked by the same production refinement validator.
    updates=[('rear','背面双环成为更大焦点，并站稳在地面上。',{
        **box(232,370,960*rearmeta['width']/rearmeta['height'],960),
        'shadow_opacity':0,'contact_shadow':{'center_x':.51,'center_y':1.004,'width':.9,'height':.012,'blur':5,'opacity':.13,'color':'#354357'}}),
        ('front','正面退到配角，保留真实角度，底部保持同一地面关系。',{
        **box(566,485,845*frontmeta['width']/frontmeta['height'],845),
        'shadow_opacity':0,'contact_shadow':{'center_x':.52,'center_y':1.004,'width':.88,'height':.013,'blur':5,'opacity':.11,'color':'#354357'}}),
        ('headline','保持语义断行，只微调字距和两行间的节奏。',{'letter_spacing':-.9,'line_height':1.12})]
    for region,goal,values in updates:
        plan,order=apply_craft_controls(plan,contract,page_id='cover',region_id=region,goal=goal,values=values)
        orders.append(order)
    report=await render_case(plan,assets,out,'02-refined');report.update(refinement_orders=orders,
        status='directed_craft_proof',production_api_approval=False,api_image_generation_calls=0)
    save(out/'refinement-report.json',report)
    print(json.dumps({'stage':'refined','path':str(out/'02-refined.png'),'issues':report['structure']},ensure_ascii=True))


if __name__=='__main__':asyncio.run(main())
