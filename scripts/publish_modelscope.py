"""Update the existing MateQuill Studio from a source-only staging directory.

Run without --publish to inspect the staged file count. Credentials are read
from hidden stdin or getpass, passed directly to the official SDK, and never
persisted with HubConfig.save_token/login. Existing Studio Secrets stay intact.
"""
from __future__ import annotations

import argparse
import getpass
import json
import logging
import os
from pathlib import Path
import shutil
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]
FORBIDDEN = {"node_modules", ".next", ".git", "__pycache__", "evidence"}


def stage(destination: Path) -> int:
    names = ("app", "web", "web-next", "prompts", "data", "docs/PLAN.html",
             "docs/SEMIFINAL_ENGINEERING.md", "docs/SEMIFINAL_RESEARCH.md", "docs/SEMIFINAL_VIDEO.md", "README.md",
             "Dockerfile", ".dockerignore", ".gitattributes", "requirements.txt", "constraints-runtime.txt",
             "scripts/run_services.py", "scripts/fetch_official_assets.py")
    count = 0
    for name in names:
        source = ROOT / name
        paths = source.rglob("*") if source.is_dir() else [source]
        for path in paths:
            relative = path.relative_to(ROOT)
            if not path.is_file() or path.is_symlink():
                continue
            if any(part in FORBIDDEN or part.startswith(".env") for part in relative.parts):
                continue
            if path.suffix in {".pyc", ".pem", ".key", ".tsbuildinfo"}:
                continue
            target = destination / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(path, target)
            count += 1
    return count


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--publish", action="store_true")
    parser.add_argument("--token-stdin", action="store_true", help="Read one secret line from stdin instead of getpass")
    parser.add_argument("--repo", default="Shuuyou/MateQuill")
    parser.add_argument("--revision", default="master")
    parser.add_argument("--sdk-path", type=Path, help="Optional isolated modelscope_hub installation")
    args = parser.parse_args()
    if args.sdk_path:
        sys.path.insert(0, str(args.sdk_path.resolve()))
    with tempfile.TemporaryDirectory(prefix="matequill-studio-", dir=ROOT / "runtime") as temporary:
        staging = Path(temporary)
        count = stage(staging)
        print(json.dumps({"source_files": count, "repo": args.repo, "publish": args.publish}), flush=True)
        if not args.publish:
            return 0
        token = sys.stdin.readline().strip() if args.token_stdin else getpass.getpass("ModelScope token (hidden): ")
        if not token:
            raise RuntimeError("No ModelScope credential provided")
        # Keep application JSON/fonts as actual Git blobs in Docker checkouts.
        # The SDK's 64 KiB default can otherwise commit LFS pointer text for
        # a JSON manifest, which Docker COPY cannot parse as product data.
        os.environ.setdefault("MODELSCOPE_UPLOAD_LFS_FORCE_THRESHOLD", "8MB")
        from modelscope_hub import HubApi
        logging.getLogger("modelscope_hub").setLevel(logging.ERROR)
        api = HubApi(token=token, endpoint="https://modelscope.cn")
        # Read first: never create a second Studio or change an unrelated account.
        api.get_repo(args.repo, repo_type="studio")
        api.upload_folder(args.repo, "studio", staging, revision=args.revision,
                          commit_message="Ship MateQuill semifinal Next.js product experience",
                          max_workers=4, use_cache=False, disable_tqdm=True,
                          sync_remote_repo=False)
        api.deploy_repo(args.repo, "studio")
        print(json.dumps({"uploaded": True, "deployment_requested": True,
                          "studio": "https://modelscope.cn/studios/" + args.repo}), flush=True)
        token = None
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        # Provider error strings can echo authenticated URLs. Report type only.
        print(json.dumps({"failed": True, "error_type": type(exc).__name__}), file=sys.stderr)
        raise SystemExit(1)
