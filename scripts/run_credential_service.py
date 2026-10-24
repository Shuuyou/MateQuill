"""Load a systemd encrypted credential into RAM, then supervise MateQuill.

The unit uses LoadCredentialEncrypted=matequill-providers:... . Provider keys
never appear in unit Environment directives, process arguments or this source.
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path


def credential_environment(directory: str, inherited=None):
    path = Path(directory) / 'matequill-providers'
    raw = path.read_bytes()
    if len(raw) > 128 * 1024:
        raise RuntimeError('Service credential payload is too large')
    values = json.loads(raw)
    if not isinstance(values, dict):
        raise RuntimeError('Service credential payload must be an object')
    environment = dict(os.environ if inherited is None else inherited)
    for name, value in values.items():
        if not isinstance(name, str) or not (name.startswith(('LLM_', 'IMAGE_', 'IMA_', 'ASR_', 'DESIGN_', 'DIVERSITY_EMBEDDING_')) or name == 'MATEQUILL_DATABASE_URL'):
            raise RuntimeError('Unexpected service credential field')
        if not isinstance(value, str) or len(value) > 8192 or '\x00' in value:
            raise RuntimeError('Invalid service credential value')
        environment[name] = value
    if not environment.get('LLM_API_KEY') or not environment.get('MATEQUILL_DATABASE_URL', '').startswith(('postgresql:', 'postgresql+psycopg:')):
        raise RuntimeError('Required production service credentials are missing')
    return environment


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--node', default='node')
    args = parser.parse_args()
    directory = os.getenv('CREDENTIALS_DIRECTORY')
    if not directory:
        parser.error('Start this entrypoint through its systemd encrypted-credential unit')
    environment = credential_environment(directory)
    root = Path(__file__).resolve().parents[1]
    executable = str(root / '.venv/bin/python')
    os.execve(executable, [executable, 'scripts/run_services.py', '--node', args.node], environment)


if __name__ == '__main__':
    main()
