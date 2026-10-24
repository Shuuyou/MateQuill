"""Versioned full release; build separately, activate only after source sign-off.

SSH key access is required. Secrets remain on the remote machine/in process
memory. Runtime tenant data is reused, never uploaded from the developer PC.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import tarfile
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from scripts import deploy_semifinal


def package_source(destination):
    # Verification builds use custom distDir names; they are generated output,
    # not source, and must not recursively enter a release archive.
    generated={path.name for path in (ROOT/'web-next').iterdir()
               if path.is_dir() and path.name.startswith('.next')}
    previous=deploy_semifinal.EXCLUDED.copy()
    try:
        deploy_semifinal.EXCLUDED.update(generated)
        deploy_semifinal.package_source(destination)
    finally:
        deploy_semifinal.EXCLUDED.clear()
        deploy_semifinal.EXCLUDED.update(previous)
NODE = '/opt/matequill-node/node-v22.23.3-linux-x64/bin/node'


def remote(host, code, payload):
    program = 'CONFIG=' + repr(payload) + '\n' + code
    completed = subprocess.run(['ssh', '-o', 'BatchMode=yes', host, 'python3 -'],
                               input=program.encode(), stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    if completed.returncode:
        raise RuntimeError('Remote release step failed; inspect its private release log')
    print(completed.stdout.decode('utf-8', 'replace').strip(), flush=True)


BUILD = r'''
import os,pathlib,subprocess,tarfile,json,shutil,time
c=CONFIG;root=pathlib.Path(c['release']);old=pathlib.Path(c['previous'])
assert root.parent==pathlib.Path('/root') and root.name.startswith('matequill-design-')
assert old.is_dir()
root.mkdir(mode=0o700,exist_ok=True)
with tarfile.open(c['archive']) as tar:
 for member in tar.getmembers():
  target=(root/member.name).resolve()
  assert target.is_relative_to(root) and not member.issym() and not member.islnk()
 tar.extractall(root,filter='data')
if (old/'research').exists() and not (root/'research').exists():(root/'research').symlink_to((old/'research').resolve(),target_is_directory=True)
runtime=root/'runtime';runtime.mkdir(exist_ok=True)
for entry in (old/'runtime').iterdir():
 if entry.name=='official-assets':continue
 if not (runtime/entry.name).exists():(runtime/entry.name).symlink_to(entry.resolve(),target_is_directory=entry.is_dir())
# Only immutable first-party images are merged; no tenant media leaves its root.
shutil.copytree(old/'runtime/official-assets',runtime/'official-assets',dirs_exist_ok=True)
env=dict(os.environ,PATH=str(pathlib.Path(c['node']).parent)+':'+os.environ['PATH'],
 NEXT_TELEMETRY_DISABLED='1',NODE_OPTIONS='--max-old-space-size=384',
 API_ORIGIN='http://127.0.0.1:'+str(c['api_port']))
log=(root/'build.log').open('ab')
commands=[['python3','-m','venv','.venv'],
 [str(root/'.venv/bin/pip'),'install','--index-url','https://mirrors.aliyun.com/pypi/simple','--timeout','120','--retries','2','--disable-pip-version-check','-q','-r','requirements.txt','-c','constraints-runtime.txt']]
for command in commands:
 subprocess.run(command,cwd=root,env=env,stdout=log,stderr=log,check=True)
# Image delivery/export uses this release's Playwright version. Installing only
# Python wheels leaves its matching browser absent even when Next is healthy.
subprocess.run([str(root/'.venv/bin/python'),'-m','playwright','install','--with-deps','chromium'],cwd=root,env=env,stdout=log,stderr=log,check=True)
subprocess.run([str(root/'.venv/bin/python'),'-c',
 "from app.poster_render import render_png; from PIL import Image; from io import BytesIO; image=render_png('<svg xmlns=\"http://www.w3.org/2000/svg\" width=\"320\" height=\"320\"><rect width=\"320\" height=\"320\" fill=\"white\"/></svg>',320,320); assert image.startswith(b'\\x89PNG\\r\\n\\x1a\\n'); assert Image.open(BytesIO(image)).size == (320,320)"],cwd=root,env=env,stdout=log,stderr=log,check=True)
web=root/'web-next';old_web=old/'web-next'
matching=all((web/n).read_bytes()==(old_web/n).read_bytes() for n in ('package.json','package-lock.json'))
if matching and (old_web/'node_modules').is_dir():
 if (web/'node_modules').exists():
  (web/'node_modules').rename(web/('node_modules.interrupted.'+str(time.time_ns())))
 shutil.copytree(old_web/'node_modules',web/'node_modules',symlinks=True)
else:
 subprocess.run(['npm','ci','--no-audit','--no-fund','--maxsockets=2'],cwd=web,env=env,stdout=log,stderr=log,check=True)
# A single build worker avoids OOM on the 1.6GB judging server. Restore the
# submitted source config afterwards; only the compiled build uses this limit.
next_config=web/'next.config.ts';original=next_config.read_text()
assert 'experimental: { proxyTimeout:' in original
next_config.write_text(original.replace('experimental: { proxyTimeout:','experimental: { cpus: 1, proxyTimeout:'))
try:
 subprocess.run(['nice','-n','10','npm','run','build'],cwd=web,env=env,stdout=log,stderr=log,check=True)
finally:
 next_config.write_text(original)
standalone=root/'web-next/.next/standalone'
shutil.copytree(root/'web-next/public',standalone/'public',dirs_exist_ok=True)
shutil.copytree(root/'web-next/.next/static',standalone/'.next/static',dirs_exist_ok=True)
assert (standalone/'server.js').is_file() and (root/'app/design_service.py').is_file()
print(json.dumps({'release':str(root),'stage':'built','tenant_runtime':'retained_remote','frontend':'Next.js standalone'}))
'''


ACTIVATE = r'''
import os,pathlib,subprocess,json,time,http.client,urllib.parse,signal
c=CONFIG;root=pathlib.Path(c['release']);old=pathlib.Path(c['previous'])
assert (root/'web-next/.next/standalone/server.js').is_file()
entries=pathlib.Path('/proc/'+str(c['previous_pid'])+'/environ').read_bytes().split(b'\0')
env=dict(os.environ)
for entry in entries:
 if b'=' in entry:
  key,value=entry.split(b'=',1);env[key.decode()]=value.decode()
env['PYTHONUNBUFFERED']='1'
if c.get('review_model'):
 env['IMAGE_REVIEW_MODEL']=c['review_model']
config=json.loads((old/'runtime/database-config.json').read_text())
backup=root/'database-before-release.dump'
from urllib.parse import urlsplit,unquote
url=urlsplit(config['owner_url'].replace('postgresql+psycopg://','postgresql://',1))
dump_env=dict(env,PGPASSWORD=unquote(url.password or ''))
log=(root/'release.log').open('ab')
if url.hostname in ('127.0.0.1','localhost','::1'):
 # FORCE RLS applies even to the application owner. A local PostgreSQL
 # administrator can back up every tenant row without changing any policy.
 with backup.open('wb') as output:
  subprocess.run(['runuser','-u','postgres','--','pg_dump','-Fc','-p',str(url.port or 5432),'-d',url.path.lstrip('/')],stdout=output,stderr=log,check=True)
else:
 subprocess.run(['pg_dump','-Fc','-h',url.hostname,'-p',str(url.port or 5432),'-U',unquote(url.username),'-d',url.path.lstrip('/'),'-f',str(backup)],env=dump_env,stdout=log,stderr=log,check=True)
subprocess.run(['pg_restore','--list',str(backup)],stdout=log,stderr=log,check=True)
backup.chmod(0o600)
migration_env=dict(env,MATEQUILL_MIGRATION_DATABASE_URL=config['owner_url'])
subprocess.run([str(root/'.venv/bin/python'),'-m','alembic','upgrade','head'],cwd=root,env=migration_env,stdout=log,stderr=log,check=True)
canonical=urllib.parse.urlsplit(env.get('MATEQUILL_PUBLIC_URL',''))
def check(port,path,frontend=False):
 headers={'Host':canonical.netloc,'X-Forwarded-Proto':'https'} if canonical.netloc else {}
 connection=http.client.HTTPConnection('127.0.0.1',port,timeout=4)
 connection.request('GET',path,headers=headers);response=connection.getresponse();body=response.read();connection.close()
 return response.status==200 and (not frontend or b'/_next/' in body)
def launch(directory,port,api_port):
 child_env=dict(env,PORT=str(port),API_PORT=str(api_port),API_ORIGIN='http://127.0.0.1:'+str(api_port))
 output=(directory/'service.log').open('ab')
 process=subprocess.Popen([str(directory/'.venv/bin/python'),'scripts/run_services.py','--node',c['node']],cwd=directory,env=child_env,stdin=subprocess.DEVNULL,stdout=output,stderr=output,start_new_session=True)
 return process
def wait_healthy(process,port,api_port):
 for _ in range(90):
  if process.poll() is not None:return False
  try:
   if check(api_port,'/health') and check(port,'/api/auth/me') and check(port,'/',True):return True
  except Exception:pass
  time.sleep(.5)
 return False
# New Next build is pinned to its own API port; validate before touching 7860.
staging=launch(root,c['staging_port'],c['api_port'])
if not wait_healthy(staging,c['staging_port'],c['api_port']):
 staging.terminate();raise RuntimeError('Staging health check failed; old service retained')
staging.terminate();staging.wait(timeout=20)
old_pid=c['previous_pid'];os.kill(old_pid,signal.SIGTERM)
for _ in range(40):
 if not pathlib.Path('/proc/'+str(old_pid)).exists():break
 time.sleep(.25)
process=launch(root,7860,c['api_port'])
if not wait_healthy(process,7860,c['api_port']):
 process.terminate();process.wait(timeout=20)
 rollback=launch(old,7860,c['previous_api_port'])
 healthy=wait_healthy(rollback,7860,c['previous_api_port'])
 print(json.dumps({'stage':'rolled_back','previous_pid':rollback.pid,'healthy':healthy}))
 raise SystemExit(1)
state={'release':str(root),'previous':str(old),'pid':process.pid,'port':7860,'api_port':c['api_port'],'backup':str(backup),'stage':'active'}
(root/'service.json').write_text(json.dumps(state))
print(json.dumps(state))
'''


ROLLBACK = r'''
import os,pathlib,subprocess,signal,json,time,http.client,urllib.parse
c=CONFIG;old=pathlib.Path(c['previous']);current=pathlib.Path(c['release'])
assert old.is_dir() and (old/'web-next/.next/standalone/server.js').is_file()
env=dict(os.environ)
for entry in pathlib.Path('/proc/'+str(c['previous_pid'])+'/environ').read_bytes().split(b'\0'):
 if b'=' in entry:
  key,value=entry.split(b'=',1);env[key.decode()]=value.decode()
env.update(PORT='7860',API_PORT=str(c['previous_api_port']),API_ORIGIN='http://127.0.0.1:'+str(c['previous_api_port']))
os.kill(c['previous_pid'],signal.SIGTERM)
for _ in range(80):
 if not pathlib.Path('/proc/'+str(c['previous_pid'])).exists():break
 time.sleep(.25)
log=(old/'service.log').open('ab')
p=subprocess.Popen([str(old/'.venv/bin/python'),'scripts/run_services.py','--node',c['node']],cwd=old,env=env,stdin=subprocess.DEVNULL,stdout=log,stderr=log,start_new_session=True)
canonical=urllib.parse.urlsplit(env.get('MATEQUILL_PUBLIC_URL',''))
for _ in range(90):
 try:
  conn=http.client.HTTPConnection('127.0.0.1',7860,timeout=3)
  conn.request('GET','/',headers={'Host':canonical.netloc,'X-Forwarded-Proto':'https'});r=conn.getresponse();body=r.read();conn.close()
  if r.status==200 and b'/_next/' in body:
   print(json.dumps({'stage':'rolled_back','pid':p.pid,'release':str(old),'database':'retained_without_destructive_downgrade'}));break
 except Exception:pass
 if p.poll() is not None:raise RuntimeError('Rollback process exited')
 time.sleep(.5)
else:raise RuntimeError('Rollback health check timed out')
'''


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--host',default='root@100.70.134.17')
    parser.add_argument('--stage',choices=['build','activate','rollback'],required=True)
    parser.add_argument('--release',required=True)
    parser.add_argument('--previous',required=True)
    parser.add_argument('--previous-pid',type=int,required=True)
    parser.add_argument('--previous-api-port',type=int,default=8002)
    parser.add_argument('--api-port',type=int,default=8003)
    parser.add_argument('--staging-port',type=int,default=7862)
    parser.add_argument('--review-model',default='',help='Explicitly validated image reviewer override at activation')
    args=parser.parse_args()
    config=vars(args)|{'node':NODE}
    if args.stage=='build':
        archive=ROOT/'runtime'/('design-release-'+time.strftime('%Y%m%d-%H%M%S')+'.tar.gz')
        package_source(archive)
        manifest=[]
        with tarfile.open(archive,'r:gz') as package:
            for member in package.getmembers():
                if member.isfile():
                    raw=package.extractfile(member).read()
                    manifest.append({'path':member.name,'bytes':len(raw),'sha256':hashlib.sha256(raw).hexdigest()})
        manifest_path=archive.with_suffix('').with_suffix('.manifest.json')
        manifest_path.write_text(json.dumps({'release':args.release,'archive':archive.name,'files':manifest},indent=2)+'\n',encoding='utf-8')
        print(json.dumps({'source_manifest':str(manifest_path),'files':len(manifest),'archive_bytes':archive.stat().st_size}),flush=True)
        destination='/tmp/'+archive.name;config['archive']=destination
        subprocess.run(['scp','-q','-o','BatchMode=yes',str(archive),args.host+':'+destination],check=True)
        remote(args.host,BUILD,config)
    elif args.stage=='activate':
        remote(args.host,ACTIVATE,config)
    else:
        remote(args.host,ROLLBACK,config)


if __name__=='__main__':
    main()
