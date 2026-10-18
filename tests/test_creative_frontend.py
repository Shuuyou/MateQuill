"""Creative previews must be actual cards, preserve disclosures, and honor audit gates."""
from test_frontend_contract import render_result


def creative_result(status='draft'):
    return {
        'mode': 'demo', 'status': status, 'title': '正文', 'blocks': [{'text': '正文内容'}],
        'assets': [{'id': 'a1', 'title': '官方接口图', 'provider': 'ima', 'verified': True}],
        'creative_plan': {
            'connector_id': 'xiaohongshu.editorial', 'connector_version': '1.0', 'platform': 'xiaohongshu',
            'cards': [
                {'id': 'c1', 'role': 'cover', 'headline': '先检查这根线', 'body': '开会之前先核对', 'layout': 'text_card', 'asset_ids': [], 'fact_ids': ['f1'], 'disclosures': ['仅限指定机型'], 'truth_status': 'concept'},
                {'id': 'c2', 'role': 'evidence', 'headline': '接口依据', 'body': '带文件到店实测', 'layout': 'photo', 'asset_ids': ['a1', 'missing'], 'fact_ids': [], 'disclosures': [], 'truth_status': 'verified', 'url': 'https://evil.example/CANARY'},
            ],
            'test_variants': [], 'promise_payoff': [],
        },
    }


def test_cards_render_visible_copy_disclosures_and_only_bound_assets():
    output = render_result(creative_result(), {'platform': 'xiaohongshu', 'mode': 'demo'})
    html = output['preview']
    assert 'creative-platform-xiaohongshu' in html
    assert 'creative-layout-text_card' in html
    assert '先检查这根线' in html and '仅限指定机型' in html
    assert '/api/assets/a1' in html
    assert '待补充匹配素材' in html and '不是实拍照片' in html
    assert 'CANARY' not in html


def test_untrusted_creative_text_is_escaped_and_blocked_run_stays_blocked():
    result = creative_result('blocked')
    result['creative_plan']['cards'][0]['headline'] = '<script>alert(1)</script>'
    output = render_result(result, {'platform': 'wechat', 'mode': 'demo'})
    assert '<script>' not in output['preview']
    assert '&lt;script&gt;' in output['preview']
    assert 'creative-platform-wechat' in output['preview']
    assert output['copyDisabled'] and output['mdDisabled'] and output['htmlDisabled']

def test_connector_selection_filters_platform_and_is_sent_with_task():
    import json
    import shutil
    import subprocess
    from pathlib import Path
    import pytest
    node = shutil.which('node')
    if not node:
        pytest.skip('Node.js is required for frontend contracts')
    script = r'''
const fs=require('fs'),vm=require('vm'),nodes=new Map();
function el(id){if(!nodes.has(id))nodes.set(id,{value:'',hidden:false,disabled:false,innerHTML:'',textContent:'',dataset:{},classList:{toggle(){},add(){},remove(){}},addEventListener(){},setAttribute(){},focus(){},showModal(){},scrollIntoView(){}});return nodes.get(id)}
const ctx={document:{querySelector:el,querySelectorAll:()=>[],getElementById:id=>el('#'+id)},fetch:async()=>({ok:true,json:async()=>({items:[]})}),console,setTimeout(){},clearTimeout(){},setInterval(){},clearInterval(){},URL,encodeURIComponent};vm.createContext(ctx);vm.runInContext(fs.readFileSync('web/app.js','utf8'),ctx);
vm.runInContext("state.creativeConnectors=[{id:'xhs.story',name:'小红书叙事',platform:'xiaohongshu',version:'1.2',styles:[{id:'suspense',name:'物件伏笔'}]},{id:'wechat.guide',name:'公众号指南',platform:'wechat',styles:['guide']}];state.platform='xiaohongshu'",ctx);
el('#creative-connector').value='xhs.story';el('#creative-style').value='suspense';vm.runInContext('renderCreativeOptions()',ctx);
const options=el('#creative-connector').innerHTML;
const task=vm.runInContext('task()',ctx);
vm.runInContext("setPlatform('wechat')",ctx);
console.log(JSON.stringify({options,task,afterPlatform:el('#creative-connector').value,styles:el('#creative-style').innerHTML}));
'''
    result = subprocess.run([node, '-e', script], cwd=Path(__file__).resolve().parents[1], capture_output=True, text=True, encoding='utf-8', check=True)
    output = json.loads(result.stdout)
    assert 'xhs.story' in output['options'] and 'wechat.guide' not in output['options']
    assert output['task']['creative_connector'] == 'xhs.story'
    assert output['task']['creative_style'] == 'suspense'
    assert output['afterPlatform'] == 'auto' and 'guide' in output['styles']
