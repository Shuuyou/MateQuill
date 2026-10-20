"""Read-only SSH preflight by default; explicitly opt in with --deploy.

SSH passwords/passphrases are read only with getpass. No credentials are saved.
The remote environment must already provide any optional model/provider secrets.
"""
from __future__ import annotations

import argparse
import getpass
import json
import os
from pathlib import Path, PurePosixPath
import shlex
import sys


def destination_within_home(home: str, destination: str) -> str:
    root = PurePosixPath(home)
    requested = PurePosixPath(destination)
    if not requested.is_absolute():
        requested = root / requested
    if ".." in requested.parts or requested == root or not requested.is_relative_to(root):
        raise ValueError("Destination must be a named subdirectory inside the SSH user's home")
    return str(requested)


def source_files(root: Path):
    """Allow source assets only, excluding local secrets and research materials."""
    for name in ("requirements.txt", "pyproject.toml", "docs/PLAN.html", "docs/PLAN.md", "docs/academic-notes.md", "docs/academic-notes.txt", "data/official_products.json", "data/official_assets.json"):
        candidate = root / name
        if candidate.is_file():
            yield candidate
    for name in ("app", "web", "prompts"):
        folder = root / name
        if not folder.is_dir():
            continue
        for candidate in sorted(folder.rglob("*")):
            relative = candidate.relative_to(root)
            if candidate.is_symlink() or any(part.startswith(".") or part in {"__pycache__", "node_modules"} for part in relative.parts):
                continue
            if candidate.is_file() and candidate.suffix.lower() not in {".pyc", ".pem", ".key", ".env"}:
                yield candidate


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host", required=True)
    parser.add_argument("--user", required=True)
    parser.add_argument("--ssh-port", type=int, default=22)
    parser.add_argument("--identity", help="Private key path; contents are never printed")
    parser.add_argument("--known-hosts", help="Optional SSH known_hosts file")
    parser.add_argument("--password", action="store_true", help="Prompt for SSH password")
    parser.add_argument("--stdin-credentials", action="store_true", help="Read a JSON object with password/passphrase from stdin; never store or print it")
    parser.add_argument("--key-passphrase", action="store_true", help="Prompt for key passphrase")
    parser.add_argument("--destination", required=True, help="Named directory beneath remote user home")
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument("--deploy", action="store_true", help="Upload, create venv, install and start server")
    args = parser.parse_args(argv)
    credentials = json.load(sys.stdin) if args.stdin_credentials else {}
    if not isinstance(credentials, dict):
        parser.error("stdin credentials must be a JSON object")
    if not 1 <= args.port <= 65535 or not 1 <= args.ssh_port <= 65535:
        parser.error("Ports must be between 1 and 65535")
    try:
        import paramiko
    except ImportError:
        parser.error("Install paramiko locally to use this optional SSH deployment tool")
    client = paramiko.SSHClient()
    client.load_system_host_keys()
    if args.known_hosts:
        client.load_host_keys(args.known_hosts)
    client.set_missing_host_key_policy(paramiko.RejectPolicy())
    try:
        client.connect(
            args.host, port=args.ssh_port, username=args.user,
            key_filename=args.identity,
            password=credentials.get("password") or (getpass.getpass("SSH password: ") if args.password else None),
            passphrase=credentials.get("passphrase") or (getpass.getpass("SSH key passphrase: ") if args.key_passphrase else None),
            timeout=15, auth_timeout=20, banner_timeout=20,
        )
        sftp = client.open_sftp()
        home = sftp.normalize(".")
        destination = destination_within_home(home, args.destination)

        def run(command: str, timeout=30):
            _, stdout, stderr = client.exec_command(command, timeout=timeout)
            output = stdout.read().decode("utf-8", "replace")
            error = stderr.read().decode("utf-8", "replace")
            status = stdout.channel.recv_exit_status()
            if status:
                # Remote shell stderr can contain credentials from environment or logs.
                raise RuntimeError(f"Remote command failed with exit code {status}; inspect server locally")
            return output.strip()

        print(run("uname -s; python3 --version; df -Pk . | tail -1"))
        print(f"Validated target: {destination}")
        if not args.deploy:
            print("Read-only preflight completed. No upload, install, directory creation, or service start was performed.")
            return 0
        root = Path(__file__).resolve().parents[1]
        if not (root / "app" / "api.py").is_file() or not (root / "requirements.txt").is_file():
            raise RuntimeError("Deployment requires app/api.py and requirements.txt")
        quote = shlex.quote
        # Refuse symlink escapes; create only after validating the resolved parent.
        safety = "import os,sys; h=os.path.realpath(sys.argv[1]); d=os.path.realpath(sys.argv[2]); assert os.path.commonpath([h,d])==h and d!=h; os.makedirs(d,exist_ok=True)"
        run(f"python3 -c {quote(safety)} {quote(home)} {quote(destination)}")
        created = {destination}
        for source in source_files(root):
            target = PurePosixPath(destination) / source.relative_to(root).as_posix()
            parent = target.parent
            missing = []
            while str(parent) not in created and str(parent) != destination:
                missing.append(parent)
                parent = parent.parent
            for folder in reversed(missing):
                try:
                    sftp.mkdir(str(folder))
                except OSError:
                    attrs = sftp.lstat(str(folder))
                    import stat
                    if not stat.S_ISDIR(attrs.st_mode):
                        raise RuntimeError("Remote deployment parent is not a directory")
                if sftp.normalize(str(folder)) != str(folder):
                    raise RuntimeError("Remote deployment directory contains a symlink")
                created.add(str(folder))
            try:
                import stat
                if stat.S_ISLNK(sftp.lstat(str(target)).st_mode):
                    raise RuntimeError("Refusing to overwrite a remote symlink")
            except FileNotFoundError:
                pass
            sftp.put(str(source), str(target))
        run(f"cd {quote(destination)} && python3 -m venv .venv && .venv/bin/python -m pip install -r requirements.txt", timeout=240)
        port_check = "import socket,sys; s=socket.socket(); s.bind(('0.0.0.0',int(sys.argv[1]))); s.close()"
        run(f"python3 -c {quote(port_check)} {args.port}")
        run(f"cd {quote(destination)} && nohup .venv/bin/python -m uvicorn app.api:app --host 0.0.0.0 --port {args.port} > server.log 2>&1 < /dev/null &", timeout=10)
        print(f"Uploaded and started uvicorn on port {args.port}. Verify health and network reachability before claiming deployment success.")
        return 0
    except Exception as exc:
        print(f"Deployment/preflight failed ({type(exc).__name__}). No credentials or remote logs printed.", file=sys.stderr)
        return 1
    finally:
        client.close()


if __name__ == "__main__":
    raise SystemExit(main())
