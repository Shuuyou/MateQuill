"""Start a current-source preview on separate ports using an isolated acceptance DB."""
import json
import os
from pathlib import Path
import shutil
import socket
import sqlite3
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parents[1]


def main():
    for port in (8026, 3004):
        with socket.socket() as probe:
            try:
                probe.bind(('127.0.0.1', port))
            except OSError:
                raise RuntimeError(f'Preview port {port} is already occupied; existing services not changed') from None
    previous = json.loads((ROOT/'runtime/final-daily-acceptance-service-8025.json').read_text(encoding='utf-8'))
    evidence = json.loads((Path(previous['cwd'])/'verification/daily-live.json').read_text(encoding='utf-8'))
    if evidence.get('passed') is not True:
        raise RuntimeError('Preview requires a passed real acceptance record')
    source = Path(previous['database']).resolve()
    if not source.is_relative_to((ROOT/'runtime').resolve()):
        raise RuntimeError('Expected an isolated acceptance database')
    folder = ROOT/'runtime'/('final-upgrade-preview-'+str(time.time_ns()))
    folder.mkdir()
    database = folder/'acceptance.sqlite3'
    with sqlite3.connect(source.resolve().as_uri()+'?mode=ro', uri=True) as src, sqlite3.connect(database) as dst:
        src.backup(dst)
    settings = json.loads((ROOT/'runtime/owned-launch-settings.json').read_text(encoding='utf-8'))
    env = dict(os.environ)
    env.update({key: str(value) for key, value in settings.items()
                if key.startswith(('LLM_', 'IMAGE_', 'ASR_', 'DESIGN_', 'DIVERSITY_EMBEDDING_'))})
    env.update(MATEQUILL_DATABASE_URL='sqlite:///'+database.as_posix(),
               MATEQUILL_ALLOWED_ORIGINS='http://127.0.0.1:3004,http://localhost:3004,http://127.0.0.1:8026',
               MATEQUILL_PUBLIC_URL='', MATEQUILL_STUDIO_PUBLIC_ORIGIN='',
               MATEQUILL_COOKIE_SECURE='0', MATEQUILL_COOKIE_SAMESITE='lax',
               MATEQUILL_DEMO_ACCOUNTS='1', ALLOW_LOCAL_SETTINGS='0',
               PYTHONIOENCODING='utf-8', API_ORIGIN='http://127.0.0.1:8026',
               MATEQUILL_BUILD_DIR='.next/final-upgrade-preview', NEXT_TELEMETRY_DISABLED='1')
    processes = []
    for name, command, cwd in [
        ('api', [sys.executable, '-m', 'uvicorn', 'app.api:app', '--host', '127.0.0.1', '--port', '8026', '--no-access-log'], ROOT),
        ('web', [shutil.which('node'), 'node_modules/next/dist/bin/next', 'dev', '-p', '3004', '--webpack'], ROOT/'web-next'),
    ]:
        with (folder/(name+'.log')).open('ab') as stream:
            process = subprocess.Popen(command, cwd=cwd, env=env, stdin=subprocess.DEVNULL,
                stdout=stream, stderr=stream, creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
        processes.append({'name': name, 'pid': process.pid})
    shutil.copyfile(source.parent/'acceptance-cookies.json', folder/'acceptance-cookies.json')
    metadata = {'processes': processes, 'folder': str(folder), 'url': 'http://127.0.0.1:3004',
                'api_origin': 'http://127.0.0.1:8026', 'database': str(database),
                'copied_from_isolated_acceptance_only': True, 'main_services_changed': False}
    (ROOT/'runtime/final-upgrade-preview.json').write_text(json.dumps(metadata, indent=2), encoding='utf-8')
    print(json.dumps(metadata))


if __name__ == '__main__':
    main()
