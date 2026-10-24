"""Build a local searchable viewer from the actually captured product manifest."""
from __future__ import annotations
import argparse
import json
from pathlib import Path
import sys

ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from app.vmall_catalog import safe_asset_url


def build(source,output):
    document=json.loads(source.read_text(encoding='utf-8'));rows=[]
    for row in document['products']:
        images=[{k:a.get(k) for k in ('image_url','sku_id','scope','width','height','download_status')}
                for a in row.get('assets',[]) if a.get('media_type')=='image' and safe_asset_url(a.get('image_url'))]
        rows.append({'id':row['spu_id'],'name':row['name'],'brand':row.get('brand',''),
                     'seller':row.get('seller',{}).get('name',''),'url':row['source_url'],
                     'variants':[{k:v.get(k) for k in ('sku_id','name')} for v in row.get('variants',[])],
                     'defaultSku':row.get('parameter_sku_id'),'images':images,
                     'parameters':row.get('parameter_sections',[])})
    payload=json.dumps(rows,ensure_ascii=False,separators=(',',':')).replace('</','<' + chr(92) + '/')
    html='''<!doctype html><html lang="zh-CN"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>MateQuill · 商城素材库</title><style>
*{box-sizing:border-box}body{margin:0;background:#f7f8f3;color:#293026;font:16px/1.65 system-ui,-apple-system,"Microsoft YaHei",sans-serif}header{padding:32px 5vw 24px;border-bottom:1px solid #dfe4d7}h1{margin:0;font-size:30px;letter-spacing:-.5px}header p{color:#61705c;margin:8px 0}main{display:grid;grid-template-columns:340px minmax(0,1fr);gap:36px;padding:28px 5vw}input,select{width:100%;background:white;border:1px solid #ced7c3;border-radius:10px;padding:12px;font:inherit;margin-bottom:10px}#list{max-height:78vh;overflow:auto}button.item{display:block;text-align:left;width:100%;border:0;border-bottom:1px solid #e0e5d9;background:transparent;padding:16px 12px;font:inherit;cursor:pointer;color:inherit}button.item:hover,button.active{background:#e6ecd9}small,.muted{color:#67735f}h2{font-size:28px;line-height:1.45;margin:0 0 12px}a{color:#3e6236}#gallery{display:grid;grid-template-columns:repeat(auto-fill,minmax(190px,1fr));gap:16px;margin-top:24px}figure{margin:0;background:white;border:1px solid #e1e6d8;border-radius:12px;overflow:hidden}figure img{display:block;width:100%;height:210px;object-fit:contain;background:#fff}figcaption{padding:10px 12px;font-size:14px}#parameters{margin-top:30px}details{border-bottom:1px solid #dfe4d7;padding:12px 0}dl{display:grid;grid-template-columns:130px 1fr;gap:10px 16px}dt{font-weight:600}dd{margin:0;white-space:pre-wrap;overflow-wrap:anywhere}#load{margin:20px 0;background:#313c29;color:white;border:0;padding:12px 20px;border-radius:10px;cursor:pointer;font:inherit}@media(max-width:800px){main{grid-template-columns:1fr}#list{max-height:260px}dl{grid-template-columns:90px 1fr}} </style>
<header><h1>商城素材库</h1><p id="stats"></p><a href="https://www.vmall.com/" target="_blank" rel="noopener">华为商城</a> · <span class="muted">2026年10月24日公开导航快照</span></header>
<main><aside><input id="query" aria-label="搜索产品" placeholder="搜索产品、品牌或销售商"><select id="brand" aria-label="品牌"><option value="">所有品牌</option></select><div id="matches" class="muted"></div><div id="list"></div></aside><section id="product"><h2 id="name"></h2><p id="seller" class="muted"></p><a id="source" target="_blank" rel="noopener">在商城查看</a><p></p><select id="sku" aria-label="选择配置"></select><div id="gallery"></div><button id="load" type="button">继续查看图片</button><div id="parameters"></div></section></main>
<script id="data" type="application/json">'''+payload+r'''</script><script>
const rows=JSON.parse(document.getElementById('data').textContent), $=id=>document.getElementById(id);
let selected=null,shown=0,pictures=[];const norm=s=>(s||'').normalize('NFKC').toLowerCase().replace(/\s/g,'');
$('stats').textContent=rows.length+' 个商品 · '+rows.reduce((a,r)=>a+r.variants.length,0)+' 个配置 · '+rows.reduce((a,r)=>a+r.images.length,0)+' 条图片引用';
for(const name of [...new Set(rows.map(r=>r.brand).filter(Boolean))].sort()){const o=document.createElement('option');o.value=o.textContent=name;$('brand').append(o)}
function list(){const q=norm($('query').value),b=$('brand').value;const found=rows.filter(r=>(!b||r.brand===b)&&norm(r.name+r.brand+r.seller).includes(q));$('matches').textContent=found.length+' 个匹配商品';$('list').replaceChildren();for(const r of found){const e=document.createElement('button');e.className='item'+(selected?.id===r.id?' active':'');e.textContent=r.name;e.onclick=()=>open(r);$('list').append(e)}return found}
function text(html){const d=new DOMParser().parseFromString(String(html||''),'text/html');d.querySelectorAll('script,style').forEach(n=>n.remove());return d.body.textContent||''}
function gallery(){const id=$('sku').value;pictures=selected.images.filter(a=>a.scope==='product_detail'||a.sku_id===id);const unique=new Map(pictures.map(a=>[a.image_url,a]));pictures=[...unique.values()];shown=0;$('gallery').replaceChildren();more();$('parameters').replaceChildren();if(id!==selected.defaultSku){const a=document.createElement('a');a.href=$('source').href;a.target='_blank';a.rel='noopener';a.textContent='查看该配置的商城资料';$('parameters').append(a);return}for(const section of selected.parameters){const d=document.createElement('details'),s=document.createElement('summary'),dl=document.createElement('dl');s.textContent=section.name||'产品参数';d.append(s,dl);for(const p of section.data||[]){const dt=document.createElement('dt'),dd=document.createElement('dd');dt.textContent=text(p.attrName);dd.textContent=text(p.attrValue)+(p.attrRemark?'\n'+text(p.attrRemark):'');dl.append(dt,dd)}$('parameters').append(d)}}
function more(){for(const a of pictures.slice(shown,shown+18)){const f=document.createElement('figure'),link=document.createElement('a'),img=document.createElement('img'),c=document.createElement('figcaption');link.href=a.image_url;link.target='_blank';link.rel='noopener';img.src=a.image_url;img.loading='lazy';img.alt=selected.name+' · '+(a.scope==='product_detail'?'详情素材':'当前配置商品图');c.textContent=a.scope==='product_detail'?'商品详情素材':'当前配置商品图';link.append(img);f.append(link,c);$('gallery').append(f)}shown=Math.min(shown+18,pictures.length);$('load').hidden=shown>=pictures.length}
function open(r){selected=r;$('name').textContent=r.name;$('seller').textContent=[r.brand,r.seller].filter(Boolean).join(' · ');$('sku').replaceChildren();for(const v of r.variants){const o=document.createElement('option');o.value=v.sku_id;o.textContent=v.name||v.sku_id;$('sku').append(o)}$('sku').value=r.defaultSku||r.variants[0]?.sku_id||'';source();gallery();list()}
function source(){$('source').href='https://item.vmall.com/product/comdetail/index.html?prdId='+encodeURIComponent(selected.id)+'&sbomCode='+encodeURIComponent($('sku').value)}
$('query').oninput=list;$('brand').onchange=list;$('sku').onchange=()=>{source();gallery()};$('load').onclick=more;
list();open(rows.find(r=>/Mate 80 Pro$/.test(r.name))||rows[0]);</script></html>'''
    output.parent.mkdir(parents=True,exist_ok=True);output.write_text(html,encoding='utf-8')
    return {'products':len(rows),'bytes':output.stat().st_size,'path':str(output)}


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--source',type=Path,default=ROOT/'data/vmall_catalog.json')
    p.add_argument('--output',type=Path,default=ROOT/'output/vmall-catalog-20261024/index.html');args=p.parse_args()
    print(json.dumps(build(args.source,args.output),ensure_ascii=True))
