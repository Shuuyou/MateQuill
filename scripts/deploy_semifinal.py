"""Deploy a versioned Next.js + FastAPI release over existing SSH key access.

The previous application stays live while the new release builds and is checked
on a separate port. Provider configuration is copied directly between remote
process environments; it is never downloaded, printed, or saved in the release.
"""
from __future__ import annotations

import argparse
from pathlib import Path
import shlex
import subprocess
import tarfile
import time

ROOT = Path(__file__).resolve().parents[1]
EXCLUDED = {"node_modules", ".next", ".git", "__pycache__", "evidence", ".env"}


def package_source(destination: Path):
    with tarfile.open(destination, "w:gz") as archive:
        for name in ("app", "web", "web-next", "prompts", "data", "docs/PLAN.html", "scripts/run_services.py", "requirements.txt", "constraints-runtime.txt", "Dockerfile"):
            folder = ROOT / name
            candidates = folder.rglob("*") if folder.is_dir() else [folder]
            for path in candidates:
                relative = path.relative_to(ROOT)
                if not path.is_file() or path.is_symlink():
                    continue
                if any(part in EXCLUDED or part.startswith(".env") for part in relative.parts):
                    continue
                if path.suffix in {".pyc", ".pem", ".key", ".tsbuildinfo"}:
                    continue
                archive.add(path, arcname=relative.as_posix())
        # Only hash-pinned product pictures accompany the private SSH archive.
        for path in (ROOT / "runtime/official-assets").rglob("*"):
            if path.is_file() and not path.is_symlink():
                archive.add(path, arcname=path.relative_to(ROOT).as_posix())


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host", required=True, help="SSH user@host")
    parser.add_argument("--previous-dir", required=True)
    parser.add_argument("--previous-pid", required=True, type=int)
    parser.add_argument("--port", type=int, default=7861)
    parser.add_argument("--api-port", type=int, default=8001)
    args = parser.parse_args()
    stamp = time.strftime("%Y%m%d-%H%M%S")
    release = f"/root/matequill-{stamp}"
    archive = ROOT / "runtime" / f"deploy-{stamp}.tar.gz"
    archive.parent.mkdir(exist_ok=True)
    package_source(archive)
    remote_archive = f"/tmp/matequill-{stamp}.tar.gz"
    subprocess.run(["scp", "-q", "-o", "BatchMode=yes", str(archive), f"{args.host}:{remote_archive}"], check=True)
    setup = f"""set -eu
mkdir -p {shlex.quote(release)}
tar -xzf {shlex.quote(remote_archive)} -C {shlex.quote(release)}
cd {shlex.quote(release)}
ln -s {shlex.quote(args.previous_dir + '/research')} research
python3 -m venv .venv
.venv/bin/pip install --index-url https://pypi.org/simple --disable-pip-version-check -q -r requirements.txt -c constraints-runtime.txt
export PATH=/opt/matequill-node/node-v22.23.3-linux-x64/bin:$PATH
export NEXT_TELEMETRY_DISABLED=1
export NODE_OPTIONS=--max-old-space-size=768
export API_ORIGIN=http://127.0.0.1:{args.api_port}
cd web-next
npm ci --no-audit --no-fund
nice -n 10 npm run build
cp -a public .next/standalone/public
mkdir -p .next/standalone/.next
cp -a .next/static .next/standalone/.next/static
"""
    # Binary stdin preserves LF on Windows; text-mode pipes translate to CRLF.
    subprocess.run(["ssh", "-o", "BatchMode=yes", args.host, "bash -s"], input=setup.encode(), check=True)
    launch = f"""import json,os,pathlib,subprocess,time,urllib.request
root=pathlib.Path({release!r})
previous=pathlib.Path('/proc/{args.previous_pid}/environ').read_bytes().split(b'\\0')
env=dict(os.environ)
for entry in previous:
    if b'=' not in entry: continue
    key,value=entry.split(b'=',1); name=key.decode()
    if name.startswith(('LLM_','IMAGE_','IMA_')): env[name]=value.decode()
env.update(PORT={str(args.port)!r},API_PORT={str(args.api_port)!r},API_ORIGIN='http://127.0.0.1:{args.api_port}',ALLOW_LOCAL_SETTINGS='0',PYTHONUNBUFFERED='1')
log=(root/'service.log').open('ab')
process=subprocess.Popen([str(root/'.venv/bin/python'),'scripts/run_services.py','--node','/opt/matequill-node/node-v22.23.3-linux-x64/bin/node'],cwd=root,env=env,stdin=subprocess.DEVNULL,stdout=log,stderr=log,start_new_session=True)
(root/'service.json').write_text(json.dumps(dict(pid=process.pid,port={args.port},api_port={args.api_port})))
for attempt in range(100):
    if process.poll() is not None: raise SystemExit('Release exited; inspect private service log')
    try:
        with urllib.request.urlopen('http://127.0.0.1:{args.port}/api/status',timeout=2) as response:
            health=json.load(response)
        with urllib.request.urlopen('http://127.0.0.1:{args.port}/',timeout=2) as response: html=response.read()
        assert b'/_next/' in html
        print(json.dumps(dict(release=str(root),pid=process.pid,port={args.port},frontend='Next.js',health=health),ensure_ascii=False));break
    except Exception: time.sleep(.5)
else: process.terminate();raise SystemExit('Release health check timed out')
"""
    subprocess.run(["ssh", "-o", "BatchMode=yes", args.host, "python3 -"], input=launch.encode(), check=True)


if __name__ == "__main__":
    main()
