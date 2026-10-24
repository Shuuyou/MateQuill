"""Build an offline human pair-review from existing text and real image files.

Input: {"cases":[{"id":"internal-id","task":{"platform":"moments",...},
"variants":[{"label":"internal-source","title":"...","blocks":[{"text":"..."}],
"images":[{"path":"relative.png","caption":"..."}]}, {...}]}]}.
Source labels are saved only in the separate operator key, never in the page.
No model calls, remote fetches, account imports, synthetic votes or telemetry.
"""
from __future__ import annotations

import argparse
import base64
import hashlib
from html import escape
from io import BytesIO
import json
from pathlib import Path
import random

from PIL import Image

MAX_IMAGE_BYTES = 12 * 1024 * 1024
MAX_CASES = 30
MAX_TOTAL_BYTES = 80 * 1024 * 1024
MIMES = {'PNG': 'image/png', 'JPEG': 'image/jpeg', 'WEBP': 'image/webp'}


def _picture(row, root):
    if not isinstance(row, dict) or not isinstance(row.get('path'), str):
        raise ValueError('Each picture requires an actual local path')
    path = (root/row['path']).resolve()
    if not path.is_relative_to(root.resolve()) or path.is_symlink() or not path.is_file():
        raise ValueError('Picture must remain inside the authorized asset root')
    if not 0 < path.stat().st_size <= MAX_IMAGE_BYTES:
        raise ValueError('Invalid picture size')
    raw = path.read_bytes()
    with Image.open(BytesIO(raw)) as image:
        if image.format not in MIMES or image.width*image.height > 30_000_000:
            raise ValueError('Only bounded PNG/JPEG/WEBP pictures can be reviewed')
        mime = MIMES[image.format]
        image.verify()
    return {'src': 'data:'+mime+';base64,'+base64.b64encode(raw).decode('ascii'),
            'caption': str(row.get('caption') or '')[:1000],
            'sha256': hashlib.sha256(raw).hexdigest(), 'bytes': len(raw)}


def _variant(value, root):
    if not isinstance(value, dict):
        raise ValueError('Variant must be an object')
    blocks = value.get('blocks') or []
    if not isinstance(blocks, list) or len(blocks) > 30:
        raise ValueError('Invalid text blocks')
    text = [str(value.get('title') or ''), str(value.get('intro') or '')]
    text += [str(block.get('text') or '') for block in blocks if isinstance(block, dict)]
    if not blocks:
        text.append(str(value.get('body') or value.get('text') or ''))
    text.append(str(value.get('cta') or ''))
    if sum(len(t) for t in text) > 24000:
        raise ValueError('Review text exceeds bounded size')
    images = value.get('images') or []
    if not isinstance(images, list) or len(images) > 9:
        raise ValueError('A review supports at most nine actual images per variant')
    return {'text': [s for s in text if s.strip()], 'images': [_picture(row, root) for row in images]}


STYLE = '''
:root{color-scheme:light;font-family:"Segoe UI","Microsoft YaHei",sans-serif;color:#293128;background:#f7f7f2}
*{box-sizing:border-box}body{margin:0}main{max-width:1360px;margin:auto;padding:56px 32px 100px}
h1{font-size:36px;line-height:1.3;letter-spacing:-.04em}p{line-height:1.8}header{max-width:850px;margin-bottom:48px}
.case{border-top:1px solid #dbe0d4;padding:28px 0 42px}.task{font-size:18px;margin-bottom:24px}.pair{display:grid;grid-template-columns:1fr 1fr;gap:24px;align-items:start}
.variant{background:white;border:1px solid #e2e6db;border-radius:18px;padding:28px;min-width:0}.variant h3{font-size:22px;margin:0 0 24px}.copy{white-space:pre-wrap;font-size:18px;line-height:1.85;overflow-wrap:anywhere}
figure{margin:22px 0}img{display:block;max-width:100%;max-height:640px;object-fit:contain;margin:auto}figcaption{font-size:16px;line-height:1.6;margin-top:12px;color:#566052}
fieldset{border:0;margin:24px 0;padding:0}legend{font-size:19px;font-weight:600;margin-bottom:12px}.choices{display:flex;flex-wrap:wrap;gap:12px}
label{font-size:17px;line-height:1.5}input{accent-color:#456538}label:has(input:checked){background:#e2eccc}label.option{padding:10px 14px;border:1px solid #d6ddcd;border-radius:10px;cursor:pointer}
textarea{width:100%;min-height:100px;font:inherit;font-size:18px;line-height:1.6;padding:16px;border:1px solid #ccd5c2;border-radius:12px;margin-top:12px;resize:vertical}
button{background:#303d2b;border:0;color:white;font:inherit;font-size:18px;padding:15px 24px;border-radius:12px;cursor:pointer}button:focus-visible,input:focus-visible,textarea:focus-visible{outline:3px solid #a7bf78;outline-offset:3px}
.footer{border-top:1px solid #dbe0d4;padding-top:28px}#notice{font-size:18px}@media(max-width:800px){main{padding:28px 18px}.pair{grid-template-columns:1fr}.variant{padding:22px}h1{font-size:30px}}
'''

CHOICES = [('A', '更愿意发 A'), ('B', '更愿意发 B'), ('tie', '两篇都可以'),
           ('neither', '两篇都不想发'), ('uncertain', '信息不够，无法判断')]


def build_review(payload, output, *, asset_root, key_path=None, seed=0):
    """Return source mapping separately; page contains no model/version labels."""
    if not isinstance(payload, dict) or not isinstance(payload.get('cases'), list) or not 1 <= len(payload['cases']) <= MAX_CASES:
        raise ValueError('Provide 1–30 comparison cases')
    output = Path(output).resolve()
    root = Path(asset_root).resolve()
    key_path = Path(key_path).resolve() if key_path else output.parent/(output.name+'.key.json')
    if key_path.is_relative_to(output):
        raise ValueError('Operator key must be outside the review page directory')
    cases, mappings, total = [], [], 0
    rng = random.Random(seed)
    for index, case in enumerate(payload['cases'], 1):
        if not isinstance(case, dict) or not isinstance(case.get('variants'), list) or len(case['variants']) != 2:
            raise ValueError('Each comparison requires exactly two actual variants')
        order = [0, 1];rng.shuffle(order)
        identifier = f'c{index:03d}'
        visible = []
        source = {}
        for label, position in zip(('A', 'B'), order):
            variant = case['variants'][position]
            projected = _variant(variant, root)
            total += sum(row['bytes'] for row in projected['images'])
            if total > MAX_TOTAL_BYTES:
                raise ValueError('Review image payload exceeds total budget')
            visible.append({'side': label, **projected})
            source[label] = {'input_index': position, 'label': str(variant.get('label') or position),
                'text_sha256': hashlib.sha256(json.dumps(projected['text'], ensure_ascii=False).encode()).hexdigest(),
                'image_sha256': [row['sha256'] for row in projected['images']]}
        task = case.get('task') if isinstance(case.get('task'), dict) else {}
        task = {k: str(task[k])[:1000] for k in ('platform', 'product', 'scene', 'goal') if task.get(k)}
        cases.append({'id': identifier, 'task': task, 'variants': visible})
        mappings.append({'case_id': identifier, 'source_case_id': str(case.get('id') or index), 'source': source})
    fragments = []
    for number, case in enumerate(cases, 1):
        task = ' · '.join(case['task'].values())
        pair = []
        for variant in case['variants']:
            words = ''.join('<p class="copy">'+escape(t)+'</p>' for t in variant['text'])
            images = ''.join('<figure><img src="'+p['src']+'" alt="待评价的实际配图"><figcaption>'+escape(p['caption'])+'</figcaption></figure>' for p in variant['images'])
            pair.append('<article class="variant"><h3>'+variant['side']+'</h3>'+words+images+'</article>')
        options = ''.join('<label class="option"><input type="radio" name="'+case['id']+'" value="'+value+'"> '+label+'</label>' for value, label in CHOICES)
        fragments.append('<section class="case" data-case="'+case['id']+'"><h2>第 '+str(number)+' 组</h2><p class="task">'+escape(task)+'</p><div class="pair">'+''.join(pair)+'</div><fieldset><legend>假如由你代表这家门店发布，你更愿意发哪篇？</legend><div class="choices">'+options+'</div></fieldset><label>哪句话、哪张图影响了你的选择？<textarea data-reason="'+case['id']+'" placeholder="也可以写：哪里不像这个身份会说的话；什么细节值得保留。"></textarea></label></section>')
    review_id = hashlib.sha256(json.dumps(cases, ensure_ascii=False, sort_keys=True).encode()).hexdigest()
    public_meta = {'schema': 'living-human-pair-review.v1', 'review_id': review_id,
                   'case_ids': [case['id'] for case in cases],
                   'evaluation_type': 'human_pairwise', 'responses': [], 'model_judgements': False}
    data = json.dumps(public_meta, ensure_ascii=False).replace('<', '\\u003c')
    script = '''const meta=__META__;
document.getElementById('save').addEventListener('click',()=>{
 const responses=meta.case_ids.map(id=>({case_id:id,preference:document.querySelector('input[name="'+id+'"]:checked')?.value||null,reason:document.querySelector('[data-reason="'+id+'"]').value.trim()}));
 const completed=responses.filter(r=>r.preference).length;
 if(!completed){document.getElementById('notice').textContent='先选择至少一组，再保存反馈。';return;}
 const value={...meta,responses,completed_cases:completed,total_cases:meta.case_ids.length,saved_at:new Date().toISOString(),incomplete:completed!==meta.case_ids.length};
 const url=URL.createObjectURL(new Blob([JSON.stringify(value,null,2)],{type:'application/json'}));
 const a=document.createElement('a');a.href=url;a.download='门店图文盲评反馈.json';a.click();setTimeout(()=>URL.revokeObjectURL(url),1000);
 document.getElementById('notice').textContent='已保存 '+completed+' 组反馈。未填写的项目仍为空。';
});'''.replace('__META__', data)
    page = '<!doctype html><html lang="zh-CN"><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1"><title>门店图文阅读对照</title><style>'+STYLE+'</style><main><header><h1>如果由你来发，你会选哪篇？</h1><p>把自己放在这家门店的日常工作里，分别读完两篇文字、看完实际配图。没有标准答案，不需要猜作者或模型。也可以认为两篇都好、都不合适，或者现有信息不足。</p><p>先按第一眼的理解阅读，再看完整内容。请保留具体的理由，不必评判文笔是否华丽。页面不联网、不上传反馈，填写后可下载给研究者。</p></header>'+''.join(fragments)+'<section class="footer"><button id="save" type="button">保存我的反馈 ↓</button><p id="notice" role="status"></p></section></main><script>'+script+'</script></html>'
    output.mkdir(parents=True, exist_ok=True)
    key_path.parent.mkdir(parents=True, exist_ok=True)
    index_path = output/'index.html'
    index_path.write_text(page, encoding='utf-8')
    key_path.write_text(json.dumps({'review_id': review_id, 'seed': seed, 'cases': mappings,
                                   'do_not_share_with_reviewers': True}, ensure_ascii=False, indent=2), encoding='utf-8')
    return {'page': str(index_path), 'operator_key': str(key_path), 'case_count': len(cases),
            'human_responses': 0, 'images_embedded': sum(len(v['images']) for c in cases for v in c['variants'])}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('input', type=Path)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--asset-root', type=Path)
    parser.add_argument('--key', type=Path)
    parser.add_argument('--seed', type=int, default=0)
    args = parser.parse_args()
    value = json.loads(args.input.read_text(encoding='utf-8'))
    print(json.dumps(build_review(value, args.output, asset_root=args.asset_root or args.input.parent,
                                  key_path=args.key, seed=args.seed), ensure_ascii=True))


if __name__ == '__main__':
    main()
