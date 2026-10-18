"""Execute the shipped JavaScript in a minimal DOM harness, without a browser.

This verifies render and handler contracts, not layout or browser accessibility.
"""
import json
from pathlib import Path
import shutil
import subprocess

import pytest


def render_result(result, submitted_task=None):
    node = shutil.which("node")
    if not node:
        pytest.skip("Node.js is required for the JavaScript DOM contract harness")
    script = r'''
const fs=require('fs'),vm=require('vm');
const nodes=new Map();
function element(id){if(!nodes.has(id))nodes.set(id,{value:'',hidden:false,disabled:false,innerHTML:'',textContent:'',dataset:{},classList:{toggle(){},add(){},remove(){}},addEventListener(){},focus(){},showModal(){},close(){},scrollIntoView(){}});return nodes.get(id)}
const sandbox={document:{querySelector:element,querySelectorAll:()=>[],getElementById:id=>element('#'+id),createElement:()=>element('created')},fetch:async path=>({ok:true,json:async()=>path==='/api/status'?{settings:{ima_configured:false,llm_configured:false}}:{items:[]}}),setTimeout:()=>0,clearTimeout(){},setInterval:()=>0,clearInterval(){},navigator:{clipboard:{writeText:async()=>{}}},console,URL,encodeURIComponent};
vm.createContext(sandbox);
vm.runInContext(fs.readFileSync('web/app.js','utf8'),sandbox);
vm.runInContext("state.platform='poster'",sandbox);
const submitted=JSON.parse(process.argv[2]);
vm.runInContext('render('+JSON.stringify(JSON.parse(process.argv[1]))+(submitted?','+JSON.stringify(submitted):'')+')',sandbox);
console.log(JSON.stringify({preview:element('#view-preview').innerHTML,facts:element('#evidence-facts').innerHTML,audit:element('#evidence-audit').innerHTML,copyDisabled:element('#copy').disabled,mdDisabled:element('#download-md').disabled,htmlDisabled:element('#download-html').disabled,notice:element('#result-notice').textContent,heading:element('#document-heading').textContent,summary:element('#result-summary').textContent}));
'''
    completed = subprocess.run([node, "-e", script, json.dumps(result), json.dumps(submitted_task)], cwd=Path(__file__).resolve().parents[1], capture_output=True, text=True, encoding="utf-8", check=True)
    return json.loads(completed.stdout)


def test_frontend_renders_clarifying_questions_and_blocks_export():
    output = render_result({"mode": "demo", "status": "needs_input", "questions": ["请补充准确型号", "请补充使用场景"]})
    assert "请补充准确型号" in output["preview"]
    assert "请补充使用场景" in output["preview"]
    assert output["copyDisabled"] and output["mdDisabled"] and output["htmlDisabled"]


def test_frontend_blocked_content_is_not_copyable_or_exportable():
    output = render_result({"mode": "live", "status": "blocked", "title": "待核验正文", "blocks": [{"text": "不能当作合格稿件交付", "fact_ids": []}]})
    assert output["copyDisabled"] and output["mdDisabled"] and output["htmlDisabled"]


def test_frontend_untrusted_markup_is_escaped_and_raw_urls_not_rendered():
    attack = '<img src=x onerror="alert(1)">'
    output = render_result({"mode": "demo", "status": "draft", "title": attack, "intro": attack, "blocks": [{"text": attack, "fact_ids": ["f1"]}], "tags": [attack], "facts": [{"id": "f1", "label": attack, "value": attack, "quote": attack, "source_id": "s1"}], "sources": [{"id": "s1", "title": attack, "url": "https://ima.qq.com/?token=CANARY_SECRET"}], "audit": [{"severity": "warning", "category": attack, "message": attack}]})
    for section in ("preview", "facts", "audit"):
        assert "<img src=x" not in output[section]
        assert "&lt;img" in output[section]
        assert "CANARY_SECRET" not in output[section]


def test_frontend_uses_submitted_platform_snapshot_instead_of_current_selection():
    output = render_result({"mode": "demo", "status": "draft", "title": "合成稿件", "blocks": []}, {"platform": "moments", "mode": "demo"})
    assert '朋友圈' in output['heading']
    assert '门店海报' not in output['heading']
    assert '朋友圈' in output['summary']

def test_model_format_failure_explains_recovery_without_changing_brief():
    output = render_result({'mode':'local','status':'blocked','blocks':[],
        'audit':[{'severity':'error','category':'model','message':'invalid_model_json'}]},
        {'product':'HUAWEI MateBook Pro','audience':'大学生','platform':'xiaohongshu','mode':'local'})
    assert '需求和资料已保留' in output['notice']
    assert '重新生成' in output['notice']
    assert output['copyDisabled'] and output['mdDisabled'] and output['htmlDisabled']
