"""Restart the actual preview listener, preserving its DB and loaded connections.

Running work requires --interrupt-running: checkpoints survive, and interrupted
turns are explicitly closed instead of silently replaying paid model requests.
"""
import argparse
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
import time

import httpx
import psutil

from scripts.start_final_upgrade_preview import ROOT, preview_environment


def close_interrupted(db, now):
    interrupted = []
    for table, kind, final in [('mq_agent_dispatch', 'agent_turn', 'failed'),
                                ('mq_job_dispatch', 'generation_job', 'cancelled')]:
        rows = db.execute(f"SELECT id,claim_token FROM {table} WHERE status IN ('queued','running')").fetchall()
        for identifier, claim in rows:
            row = db.execute('SELECT body FROM mq_resources WHERE kind=? AND id=?', (kind, identifier)).fetchone()
            if not row:
                raise RuntimeError('Active dispatch has no resource checkpoint')
            body = json.loads(row[0])
            message = '服务已升级，本轮未完成的任务已停止，已保存的资料和作品保留。'
            body.update(status=final, error={'code': 'service_restarted', 'message': message}, updated_at=now)
            if kind == 'agent_turn':
                body['message'] = message
                cid = body['conversation_id']
                saved = db.execute("SELECT body FROM mq_resources WHERE kind='agent_conversation' AND id=?", (cid,)).fetchone()
                conversation = json.loads(saved[0])
                if conversation.get('active_turn_id') == identifier:
                    conversation.update(active_turn_id=None, pending_questions=[], updated_at=now)
                    conversation['messages'].append({'id': identifier+':assistant', 'role': 'assistant',
                        'text': message, 'created_at': now})
                    db.execute("UPDATE mq_resources SET body=?,version=version+1,updated_at=? WHERE kind='agent_conversation' AND id=?",
                               (json.dumps(conversation, ensure_ascii=False), now, cid))
            db.execute('UPDATE mq_resources SET body=?,version=version+1,updated_at=? WHERE kind=? AND id=?',
                       (json.dumps(body, ensure_ascii=False), now, kind, identifier))
            db.execute(f'UPDATE {table} SET status=?,lease_until=0 WHERE id=? AND claim_token IS ?',
                       (final, identifier, claim))
            interrupted.append({'id': identifier, 'kind': kind, 'status': final})
    return interrupted


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--interrupt-running', action='store_true')
    args = parser.parse_args()
    metadata = ROOT/'runtime/final-upgrade-preview.json'
    meta = json.loads(metadata.read_text(encoding='utf8'))
    database = Path(meta['database']).resolve()
    assert database.is_relative_to((ROOT/'runtime').resolve())
    listeners = []
    for process in psutil.process_iter(['pid', 'cmdline']):
        if '8026' not in (process.info['cmdline'] or []):
            continue
        if Path(process.cwd()).resolve() != ROOT.resolve():
            continue
        env = process.environ()
        if env.get('MATEQUILL_DATABASE_URL') == 'sqlite:///'+database.as_posix():
            listeners.append((process, env))
    if len(listeners) != 1:
        raise RuntimeError('Expected one owned API listener on 8026')
    process, env = listeners[0]
    with sqlite3.connect(database) as db:
        active = sum(db.execute(f"SELECT COUNT(*) FROM {table} WHERE status IN ('queued','running')").fetchone()[0]
                     for table in ('mq_agent_dispatch', 'mq_job_dispatch'))
    if active and not args.interrupt_running:
        raise RuntimeError('Active work exists; explicit maintenance interruption is required')
    settings = json.loads((ROOT/'runtime/owned-launch-settings.json').read_text(encoding='utf8'))
    role_keys = ('MATEQUILL_FACT_REVIEW_USE_PLANNER', 'MATEQUILL_MARKETING_REVIEW_USE_PLANNER')
    env = preview_environment({key: settings[key] for key in role_keys if key in settings}, env)
    env['MATEQUILL_BUILD_REVISION'] = subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=ROOT, text=True).strip()
    executable = process.exe()
    subprocess.run([executable, '-c', 'import app.api; import app.gtm_agent.runtime; import app.design_service'],
                   cwd=ROOT, env=env, check=True, capture_output=True)
    process.terminate()
    process.wait(10)
    with sqlite3.connect(database) as db:
        interrupted = close_interrupted(db, time.time())
    with (Path(meta['folder'])/'api.log').open('ab') as log:
        launched = subprocess.Popen([executable, '-m', 'uvicorn', 'app.api:app', '--host', '127.0.0.1',
            '--port', '8026', '--no-access-log'], cwd=ROOT, env=env, stdin=subprocess.DEVNULL,
            stdout=log, stderr=log, creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
    for item in meta['processes']:
        if item['name'] == 'api': item['pid'] = launched.pid
    meta.update(review_roles={key: env[key] == '1' for key in role_keys},
                build_revision=env['MATEQUILL_BUILD_REVISION'])
    metadata.write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding='utf8')
    report = {'pid': launched.pid, 'revision': meta['build_revision'], 'interrupted': interrupted}
    for _ in range(60):
        try:
            if httpx.get(meta['api_origin']+'/health', timeout=2, trust_env=False).status_code == 200:
                report['health'] = 200
                break
        except httpx.RequestError:
            pass
        time.sleep(.5)
    (ROOT/'runtime/preview-maintenance-20261025.json').write_text(json.dumps(report, indent=2), encoding='utf8')
    if report.get('health') != 200:
        raise RuntimeError('Restarted service has not passed health verification')
    print(json.dumps(report))


if __name__ == '__main__':
    main()
