"""Freeze a private isolated API and exercise one actual daily preparation.

Provider settings only enter subprocess memory. This never seeds generated
results, edits a business DB, restarts the main app or repeats a creative POST.
"""
from __future__ import annotations
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import secrets
import shutil
import socket
import subprocess
import sys
import time

import httpx

ROOT = Path(__file__).resolve().parents[1]
PORT = int(os.environ.get('FINAL_ACCEPTANCE_PORT', '8024'))
ORIGIN = f'http://127.0.0.1:{PORT}'


def write(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding='utf-8')


def snapshot():
    with socket.socket() as probe:
        try:
            probe.bind(('127.0.0.1', PORT))
        except OSError:
            raise RuntimeError('Port 8024 belongs to an existing process; not changed') from None
    stamp = datetime.now(timezone.utc).strftime('%Y%m%d-%H%M%S')
    target = ROOT/'runtime'/('final-acceptance-'+stamp)
    target.mkdir()
    files = []
    for folder in ('app', 'data', 'prompts'):
        for source in sorted((ROOT/folder).rglob('*')):
            relative = source.relative_to(ROOT)
            if not source.is_file() or source.is_symlink() or '__pycache__' in relative.parts or source.suffix in {'.pyc', '.db', '.sqlite', '.sqlite3'}:
                continue
            raw = source.read_bytes()
            destination = target/relative
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_bytes(raw)
            files.append({'path': relative.as_posix(), 'bytes': len(raw), 'sha256': hashlib.sha256(raw).hexdigest()})
    scripts = target/'scripts';scripts.mkdir()
    raw = (ROOT/'scripts/tenant_admin.py').read_bytes()
    (scripts/'tenant_admin.py').write_bytes(raw)
    files.append({'path': 'scripts/tenant_admin.py', 'bytes': len(raw), 'sha256': hashlib.sha256(raw).hexdigest()})
    assets = ROOT/'runtime/official-assets'
    if assets.is_dir():
        shutil.copytree(assets, target/'runtime/official-assets')
    write(target/'source-manifest.json', {'frozen_at': time.time(), 'files': files,
        'copied_runtime': ['official-assets'], 'copied_business_database': False,
        'copied_provider_credentials': False, 'knowledge_source': 'captured official product records and hash-pinned official images'})
    return target


SEED = '''import json,sys
from scripts.tenant_admin import bootstrap
from app.tenancy import TenantContext,set_context,reset_context
from app.store_campaigns import StoreInput,StoreRepository
value=json.load(sys.stdin)
account=bootstrap(email=value['email'],name='决赛验收运营',tenant_name='决赛隔离验收',password=value['password'])
token=set_context(TenantContext(account['user_id'],account['tenant_id'],roles=('admin',),all_stores=True,grants=(('admin',None),)))
try:
 shops=[]
 for name,location in [('福雷德广场店','杭州市钱塘区白杨街道福雷德广场'),('杭州来福士店','杭州市上城区新业路杭州来福士中心')]:
  shops.append(StoreRepository().save_store(StoreInput(name=name,location=location,audience='由Agent选择')))
finally:reset_context(token)
print(json.dumps({'account':account,'stores':shops},ensure_ascii=False))
'''


def resource(database, kind, identifier):
    import sqlite3
    with sqlite3.connect(database.resolve().as_uri()+'?mode=ro', uri=True) as connection:
        row = connection.execute('SELECT body FROM mq_resources WHERE kind=? AND id=?', (kind, identifier)).fetchone()
    return json.loads(row[0]) if row else None


def main():
    frozen = snapshot()
    output = frozen/'verification';output.mkdir()
    database = frozen/'runtime/acceptance.sqlite3'
    environment = dict(os.environ)
    saved = json.loads((ROOT/'runtime/owned-launch-settings.json').read_text(encoding='utf-8'))
    for key, value in saved.items():
        if key.startswith(('LLM_', 'IMAGE_', 'DESIGN_', 'ASR_')):
            environment[key] = str(value)
    environment.update(MATEQUILL_DATABASE_URL='sqlite:///'+database.as_posix(),
        MATEQUILL_ALLOWED_ORIGINS=ORIGIN+',http://127.0.0.1:3003', MATEQUILL_PUBLIC_URL='',
        MATEQUILL_STUDIO_PUBLIC_ORIGIN='', MATEQUILL_COOKIE_SECURE='0',
        MATEQUILL_COOKIE_SAMESITE='lax', MATEQUILL_DEMO_ACCOUNTS='0',
        ALLOW_LOCAL_SETTINGS='0', PYTHONUNBUFFERED='1', PYTHONIOENCODING='utf-8')
    # No experiment/model secrets are written, even to this ignored snapshot.
    password = secrets.token_urlsafe(24)
    email = 'final-daily-'+secrets.token_hex(5)+'@acceptance.matequill.invalid'
    seed = subprocess.run([sys.executable, '-c', SEED], cwd=frozen, env=environment,
        input=json.dumps({'email': email, 'password': password}).encode(),
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False)
    if seed.returncode:
        raise RuntimeError('Isolated identity seeding failed: '+seed.stderr.decode('utf-8', 'replace')[-500:])
    identities = json.loads(seed.stdout)
    logfile = frozen/'runtime/api.log'
    with logfile.open('ab') as stream:
        process = subprocess.Popen([sys.executable, '-m', 'uvicorn', 'app.api:app', '--host', '127.0.0.1',
            '--port', str(PORT), '--no-access-log'], cwd=frozen, env=environment,
            stdin=subprocess.DEVNULL, stdout=stream, stderr=stream,
            creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
    metadata = {'pid': process.pid, 'cwd': str(frozen), 'port': PORT, 'started_at': time.time(),
                'database': str(database), 'log': str(logfile), 'source_manifest': str(frozen/'source-manifest.json')}
    write(ROOT/'runtime'/f'final-daily-acceptance-service-{PORT}.json', metadata)
    if PORT == 8024:
        write(ROOT/'runtime/final-daily-acceptance-service.json', metadata)
    result = {'started_at': time.time(), 'service': metadata, 'steps': [], 'submitted_once': False,
              'passed': False, 'identities_seeded': 1, 'stores_seeded': identities['stores']}
    report = output/'daily-live.json'
    def save(): write(report, result)
    save()
    with httpx.Client(base_url=ORIGIN, headers={'Origin': ORIGIN}, timeout=30, trust_env=False) as client:
        for _ in range(120):
            if process.poll() is not None:
                raise RuntimeError('Isolated API exited; see private API log')
            try:
                if client.get('/health').status_code == 200:
                    break
            except httpx.HTTPError:
                pass
            time.sleep(.5)
        else:raise TimeoutError('Isolated API startup exceeded 60 seconds')
        def call(method, path, **kwargs):
            response = client.request(method, path, **kwargs)
            result['steps'].append({'method': method, 'path': path, 'http': response.status_code})
            save()
            response.raise_for_status()
            return response.json()
        try:
            call('POST', '/api/auth/login', json={'email': email, 'password': password})
            password = None
            account = identities['account'];stores = identities['stores']
            call('POST', '/api/auth/workspace', json={'tenant_id': account['tenant_id'], 'store_id': stores[0]['id']})
            write(frozen/'runtime/acceptance-cookies.json', [{'name': c.name, 'value': c.value,
                'domain': c.domain, 'path': c.path} for c in client.cookies.jar])
            assert call('GET', '/api/channel/assignments')['assignments'] == []
            assert call('GET', '/api/store/daily')['status'] == 'idle'
            first = call('POST', '/api/store/daily/prepare', json={})
            result.update(submitted_once=True, first=first, submitted_at=time.time())
            save()
            print(json.dumps({'stage':'submitted','port':PORT,'conversation_id':first['conversation_id'],
                'turn_id':first['turn_id'],'report':str(report)}, ensure_ascii=False), flush=True)
            deadline = time.monotonic()+600
            while time.monotonic() < deadline:
                current = call('GET', '/api/store/daily')
                result['latest'] = current
                if current['status'] not in {'preparing'} and not current.get('in_progress'):
                    break
                time.sleep(3)
            else:result['deadline_reached'] = True
            turn = resource(database, 'agent_turn', first['turn_id'])
            conversation = call('GET', '/api/agent/conversations/'+first['conversation_id'])
            result['turn'] = turn
            result['conversation'] = conversation
            result['tool_calls'] = [entry.get('tool') for entry in (turn or {}).get('observations', [])]
            result['elapsed_seconds'] = time.time()-result['submitted_at']
            artifact = current.get('artifact')
            if artifact and artifact['kind'] == 'run':
                result['run'] = call('GET', '/api/runs/'+artifact['id'])
            if current['status'] in {'ready', 'proposal', 'needs_input'}:
                adopted = call('POST', '/api/store/daily/'+first['id']+'/adopt', json={})
                result['adoption_same_conversation'] = adopted['conversation_id'] == first['conversation_id']
            for _ in range(3):
                observed = call('GET', '/api/store/daily')
                assert observed.get('turn_id') == first['turn_id']
            before = len((resource(database, 'agent_turn', first['turn_id']) or {}).get('observations', []))
            call('GET', '/api/store/daily')
            after = len((resource(database, 'agent_turn', first['turn_id']) or {}).get('observations', []))
            result['read_only_no_tool_replay'] = before == after
            call('POST', '/api/auth/workspace', json={'tenant_id': account['tenant_id'], 'store_id': stores[1]['id']})
            other = call('GET', '/api/store/daily')
            result['second_store_idle_without_model_call'] = other['status'] == 'idle'
            denied = client.post('/api/store/daily/'+first['id']+'/adopt', json={})
            result['cross_store_adoption_http'] = denied.status_code
            assert denied.status_code == 404
            call('POST', '/api/auth/workspace', json={'tenant_id': account['tenant_id'], 'store_id': stores[0]['id']})
            write(frozen/'runtime/acceptance-cookies.json', [{'name': c.name, 'value': c.value,
                'domain': c.domain, 'path': c.path} for c in client.cookies.jar])
            result['passed'] = bool(current['status']=='ready' and (result.get('run') or {}).get('status')=='ready'
                and result.get('adoption_same_conversation') and result['read_only_no_tool_replay']
                and result['second_store_idle_without_model_call'])
        except Exception as exc:
            # Store exception class only; provider messages can contain private URLs.
            result['failure'] = type(exc).__name__
        result['finished_at'] = time.time()
        save()
    print(json.dumps({'passed':result['passed'],'status':result.get('latest',{}).get('status'),
        'tools':result.get('tool_calls'),'report':str(report),'service_left_running':True},ensure_ascii=False),flush=True)


if __name__ == '__main__':
    main()
