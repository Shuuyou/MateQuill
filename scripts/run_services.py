"""Supervise the standalone Next.js frontend and loopback FastAPI worker.

Provider credentials are inherited through the process environment. Either child
exiting terminates its sibling so a host orchestrator can restart the whole app.
"""
from __future__ import annotations

import argparse
import os
from pathlib import Path
import signal
import subprocess
import sys
import time
import urllib.request


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--frontend", default="web-next/.next/standalone")
    parser.add_argument("--node", default="node")
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    frontend = (root / args.frontend).resolve()
    if not (frontend / "server.js").is_file():
        parser.error("Build the standalone Next.js frontend before starting services")
    api_port = int(os.environ.get("API_PORT", "8000"))
    frontend_port = int(os.environ.get("PORT", "7860"))
    environment = dict(os.environ)
    environment.setdefault("ALLOW_LOCAL_SETTINGS", "0")
    environment.setdefault("ASR_BASE_URL", environment.get("IMAGE_BASE_URL", ""))
    environment.setdefault("ASR_API_KEY", environment.get("IMAGE_API_KEY", ""))
    environment.setdefault("ASR_MODEL", "gpt-4o-mini-transcribe")
    children: list[subprocess.Popen] = []
    stopping = False

    def stop(_signal=None, _frame=None):
        nonlocal stopping
        stopping = True
        for process in children:
            if process.poll() is None:
                process.terminate()

    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    try:
        children.append(subprocess.Popen([
            sys.executable, "-m", "uvicorn", "app.api:app", "--host", "127.0.0.1",
            "--port", str(api_port), "--no-proxy-headers", "--no-access-log",
        ], cwd=root, env=environment))
        for _ in range(100):
            if stopping or children[0].poll() is not None:
                return 1
            try:
                with urllib.request.urlopen(f"http://127.0.0.1:{api_port}/health", timeout=1) as response:
                    if response.status == 200:
                        break
            except Exception:
                time.sleep(0.2)
        else:
            raise RuntimeError("API did not become healthy")
        frontend_environment = dict(environment, HOSTNAME="0.0.0.0", PORT=str(frontend_port), NODE_ENV="production")
        children.append(subprocess.Popen([args.node, "server.js"], cwd=frontend, env=frontend_environment))
        while not stopping and all(process.poll() is None for process in children):
            time.sleep(0.5)
        return 0 if stopping else 1
    finally:
        stop()
        for process in children:
            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait()


if __name__ == "__main__":
    raise SystemExit(main())
