"""Real cold conversation -> researched copy -> reviewed images -> PNG exports.

Posts a single normal user request, records API outcomes and timings, and never
substitutes fixtures or marks an incomplete result as a successful acceptance.
"""
import hashlib
import argparse
import json
import re
from pathlib import Path
import time
from uuid import uuid4

import httpx
from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
FOLDER = ROOT/'runtime/full-chain-acceptance-20261025'
OUTPUT = ROOT/'output/full-chain-acceptance-20261025'
TEXT = '帮我为华为手环 10制作适合本店的小红书图文。请自行调研本店周边并选择具体生活场景，文案和配图都要完成。'


def main():
    global FOLDER, OUTPUT
    parser = argparse.ArgumentParser()
    parser.add_argument('--attempt', default='initial')
    args = parser.parse_args()
    if not re.fullmatch(r'[a-z0-9-]{1,40}', args.attempt):
        parser.error('Attempt name must be a short lowercase identifier')
    if args.attempt != 'initial':
        FOLDER = FOLDER/args.attempt
        OUTPUT = OUTPUT/args.attempt
    FOLDER.mkdir(parents=True, exist_ok=True)
    OUTPUT.mkdir(parents=True, exist_ok=True)
    report_path = FOLDER/'report.json'
    if report_path.exists():
        raise RuntimeError('A live attempt already exists; inspect it rather than silently reposting')
    report = {'passed': False, 'request': TEXT, 'attempt': args.attempt,
              'started_at': time.time(), 'posted_once': False}
    def save(): report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf8')
    save()
    with httpx.Client(base_url='http://127.0.0.1:3004', headers={'Origin': 'http://127.0.0.1:3004'},
                      timeout=40, trust_env=False) as client:
        def call(method, path, **kwargs):
            response = client.request(method, path, **kwargs)
            response.raise_for_status()
            return response.json()
        try:
            call('POST', '/api/auth/demo', json={'persona': 'fuleide'})
            runtime = call('GET', '/api/runtime/status')
            assert runtime['review_roles'] == {'fact': True, 'marketing': True}
            assert runtime['models']['visual_review'] == 'gpt-4o'
            report['runtime_before'] = runtime
            conversation = call('POST', '/api/agent/conversations', json={'title': '完整新任务验收 · 华为手环10'})
            cid = conversation['id']
            assert not conversation['messages'] and not conversation['current_artifact'] and not conversation.get('current_opportunity')
            report['conversation_id'] = cid
            report['cold_conversation'] = True
            report['submitted_at'] = time.time()
            turn = call('POST', f'/api/agent/conversations/{cid}/turns', json={
                'text': TEXT, 'media_ids': [], 'client_turn_id': 'full-chain-'+uuid4().hex})
            report.update(posted_once=True, turn_id=turn['id'])
            save()
            print(json.dumps({'stage': 'submitted', 'conversation': cid, 'turn': turn['id']}), flush=True)
            last = None
            deadline = time.monotonic()+1000
            while True:
                turn = call('GET', f'/api/agent/conversations/{cid}/turns/{turn["id"]}')
                progress = turn.get('progress') or {}
                signature = (turn['status'], progress.get('stage'))
                if signature != last:
                    event = {'elapsed_seconds': round(time.time()-report['submitted_at'], 2),
                             'status': turn['status'], 'stage': progress.get('stage')}
                    report.setdefault('progress', []).append(event)
                    print(json.dumps(event), flush=True)
                    last = signature
                report['outcome'] = turn
                save()
                if turn['status'] not in {'queued', 'running'}:
                    break
                if time.monotonic() > deadline:
                    raise TimeoutError('Live task exceeded the acceptance deadline; not marked passed')
                time.sleep(2)
            report['generation_seconds'] = round(time.time()-report['submitted_at'], 2)
            assert turn['status'] == 'completed', turn
            artifact = turn.get('artifact') or {}
            assert artifact.get('kind') == 'design', 'Both copy and images were requested'
            design = call('GET', '/api/designs/'+artifact['id'])
            assert design['status'] == 'ready' and design['artboards']
            report['design'] = design
            source = call('GET', '/api/runs/'+design['source_run_id'])
            assert source['status'] == 'ready'
            assert not [row for row in source.get('audit', []) if row.get('severity') == 'error']
            report['source_run_id'] = design['source_run_id']
            report['copy_title'] = source.get('title')
            report['text_timing'] = source.get('strategy', {}).get('generation_timing')
            (OUTPUT/'copy.json').write_text(json.dumps(source, ensure_ascii=False, indent=2), encoding='utf8')
            exports = []
            for index, board in enumerate(design['artboards'], 1):
                response = client.get(board['export_urls']['png'])
                response.raise_for_status()
                path = OUTPUT/f'card-{index:02}.png'
                path.write_bytes(response.content)
                with Image.open(path) as picture:
                    picture.verify()
                with Image.open(path) as picture:
                    dimensions = picture.size
                assert min(dimensions) >= 600
                exports.append({'path': str(path), 'sha256': hashlib.sha256(response.content).hexdigest(),
                                'dimensions': dimensions, 'bytes': len(response.content)})
            report['exports'] = exports
            report['runtime_after'] = call('GET', '/api/runtime/status')
            assert report['runtime_after']['review_roles'] == runtime['review_roles']
            assert report['runtime_after']['models'] == runtime['models']
            assert report['runtime_after']['build_revision'] == runtime['build_revision']
            report['passed'] = True
            report['total_seconds_including_exports'] = round(time.time()-report['submitted_at'], 2)
            print(json.dumps({'stage': 'passed', 'seconds': report['generation_seconds'], 'images': len(exports)}), flush=True)
        except Exception as exc:
            report['failure'] = {'type': type(exc).__name__, 'message': str(exc)[:1600]}
            print(json.dumps({'stage': 'failed', 'type': type(exc).__name__}), flush=True)
            raise
        finally:
            save()


if __name__ == '__main__':
    main()
