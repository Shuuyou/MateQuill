"""Exercise poster controls and edit boundaries in the shipped JS, without APIs."""
import json
from pathlib import Path
import shutil
import subprocess

import pytest


def test_poster_brief_dimensions_and_product_layer_lock():
    node = shutil.which('node')
    if not node:
        pytest.skip('Node.js is required for frontend contracts')
    script = r'''
const fs=require('fs'),vm=require('vm'),nodes=new Map();
function el(id){if(!nodes.has(id))nodes.set(id,{value:'',hidden:false,disabled:false,innerHTML:'',textContent:'',dataset:{},style:{},classList:{toggle(){},add(){},remove(){}},addEventListener(){},setAttribute(){},focus(){},showModal(){},scrollIntoView(){}});return nodes.get(id)}
const ctx={document:{querySelector:el,querySelectorAll:()=>[],getElementById:id=>el('#'+id)},fetch:async path=>({ok:true,json:async()=>path==='/api/status'?{settings:{}}:{models:[]}}),console,setTimeout(){},clearTimeout(){},setInterval(){},clearInterval(){},URL,encodeURIComponent,structuredClone};vm.createContext(ctx);vm.runInContext(fs.readFileSync('web/app.js','utf8'),ctx);
vm.runInContext("setPlatform('poster')",ctx);const visible=!el('#poster-brief').hidden;
el('#poster-size').value='story';el('#poster-purpose').value='社群引流';el('#poster-background').value='solid';
const brief=vm.runInContext('posterBrief()',ctx);
vm.runInContext("posterState.document={layers:[{id:'product',type:'image',name:'<img onerror=x>',locked:true,x:20,y:30},{id:'headline',type:'text',text:'标题',locked:false,x:40,y:50,font_size:60,color:'#112233'}]};selectPosterLayer('product')",ctx);
const productLocked=el('#poster-apply-layer').disabled&&el('#layer-text').disabled;
const escaped=el('#poster-layers').innerHTML.includes('&lt;img');
vm.runInContext("selectPosterLayer('headline')",ctx);const textEditable=!el('#poster-apply-layer').disabled&&!el('#layer-text').disabled;
vm.runInContext("setPlatform('wechat')",ctx);
console.log(JSON.stringify({visible,brief,productLocked,textEditable,escaped,hidden:el('#poster-brief').hidden}));
'''
    result = subprocess.run([node, '-e', script], cwd=Path(__file__).resolve().parents[1], capture_output=True, text=True, encoding='utf-8', check=True)
    output = json.loads(result.stdout)
    assert output['visible'] and output['hidden']
    assert (output['brief']['width'], output['brief']['height']) == (1080, 1920)
    assert output['brief']['background_mode'] == 'solid'
    assert output['productLocked'] and output['textEditable'] and output['escaped']


def test_undo_uses_latest_server_version_and_generation_shows_audit_error():
    node = shutil.which('node')
    if not node:
        pytest.skip('Node.js is required for frontend contracts')
    script = r'''
const fs=require('fs'),vm=require('vm'),nodes=new Map(),patches=[];
function el(id){if(!nodes.has(id))nodes.set(id,{value:'',hidden:false,disabled:false,innerHTML:'',textContent:'',dataset:{},style:{},classList:{toggle(){},add(){},remove(){}},addEventListener(){},setAttribute(){},focus(){},showModal(){},scrollIntoView(){}});return nodes.get(id)}
const ctx={document:{querySelector:el,querySelectorAll:()=>[],getElementById:id=>el('#'+id)},fetch:async(path,options={})=>({ok:true,json:async()=>{if(options.method==='PATCH'){const doc=JSON.parse(options.body).document;patches.push(doc);return{id:'poster-1',document:{...doc,version:doc.version+1}}}if(path==='/api/posters')return{status:'blocked',audit:[{severity:'error',message:'缺少已核验的产品主视觉'}]};return path==='/api/status'?{settings:{}}:{models:[]}}}),console,setTimeout(){},clearTimeout(){},setInterval(){},clearInterval(){},URL,encodeURIComponent,structuredClone};
vm.createContext(ctx);vm.runInContext(fs.readFileSync('web/app.js','utf8'),ctx);
(async()=>{
vm.runInContext("renderPoster=()=>{};posterState.result={id:'poster-1',document:{version:2}};posterState.document={version:2,layers:[{id:'headline',text:'new'}]};posterState.undo=[{version:1,layers:[{id:'headline',text:'old'}]}]",ctx);
await vm.runInContext('undoPoster()',ctx);
const version=vm.runInContext('posterState.document.version',ctx),remaining=vm.runInContext('posterState.undo.length',ctx);
el('#poster-size').value='square';el('#poster-background').value='solid';await vm.runInContext('generatePoster()',ctx);
console.log(JSON.stringify({sentVersion:patches[0].version,text:patches[0].layers[0].text,version,remaining,error:el('#task-error').textContent}));
})().catch(e=>{console.error(e);process.exitCode=1});
'''
    result = subprocess.run([node, '-e', script], cwd=Path(__file__).resolve().parents[1], capture_output=True, text=True, encoding='utf-8', check=True)
    output = json.loads(result.stdout)
    assert output['sentVersion'] == 2
    assert output['version'] == 3
    assert output['text'] == 'old' and output['remaining'] == 0
    assert '缺少已核验的产品主视觉' in output['error']
