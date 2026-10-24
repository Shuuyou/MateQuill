"""Export final image source copy from isolated benchmark SQLite, read-only.

The completed image artifact selects the run. Tenant/store/private-owner scope
comes solely from that case report. This never promotes an intermediate run.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import sqlite3
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scripts.benchmark_store_cold_start import EvidenceWriter
from scripts.render_six_dimension_benchmark import public_copy, read_json, within


def _write_consistent(path, raw):
    if path.exists():
        if path.read_bytes() != raw:
            raise ValueError('existing_export_conflicts_with_final_source')
        return
    path.write_bytes(raw)


def export_case(root: Path, case: dict):
    import re
    case_id = case.get('case_id', '')
    if not re.fullmatch(r'[A-Za-z0-9_-]{1,100}', case_id):
        raise ValueError('unsafe_case_id')
    directory = root / case_id
    if not within(directory, root):
        raise ValueError('case_outside_root')
    report = read_json(directory / 'report.json') or {}
    stage = (report.get('stages') or {}).get('image') or {}
    artifact = stage.get('artifact') or {}
    design = read_json(directory / 'image-design.json') or {}
    if (not stage.get('ready') or stage.get('turn_status') != 'completed' or artifact.get('kind') != 'design'
            or not artifact.get('id') or design.get('id') != artifact['id'] or design.get('status') != 'ready'):
        return {'case_id': case_id, 'status': 'skipped', 'reason': 'no_ready_final_image_artifact'}
    source_id = design.get('source_run_id')
    account = report.get('account') or {}
    tenant_id, user_id, store_id = account.get('tenant_id'), account.get('user_id'), report.get('store_id')
    if not all(isinstance(value, str) and value for value in (source_id, tenant_id, user_id, store_id)):
        return {'case_id': case_id, 'status': 'skipped', 'reason': 'missing_authorized_scope_or_source'}
    database = directory / 'benchmark.sqlite3'
    # Never follow a report path or symlink into another database, production or
    # another case. Standard benchmark cases use this exact isolated filename.
    report_database = Path(report.get('database_file', '')).resolve()
    if (not database.is_file() or not within(database, directory)
            or report_database != database.resolve()):
        return {'case_id': case_id, 'status': 'skipped', 'reason': 'database_not_isolated_case_file'}
    uri = database.resolve().as_uri() + '?mode=ro'
    with sqlite3.connect(uri, uri=True, timeout=10) as connection:
        connection.execute('PRAGMA query_only = ON')
        row = connection.execute('SELECT body, store_id, owner_id, visibility FROM mq_resources '
                                 'WHERE kind = ? AND id = ? AND tenant_id = ?',
                                 ('run', source_id, tenant_id)).fetchone()
    if row is None or row[1] != store_id or (row[3] == 'private' and row[2] != user_id):
        return {'case_id': case_id, 'status': 'skipped', 'reason': 'source_not_in_authorized_scope'}
    source = json.loads(row[0]) if isinstance(row[0], str) else None
    if (not isinstance(source, dict) or source.get('status') != 'ready'
            or (source.get('run_id') or source.get('id')) != source_id):
        return {'case_id': case_id, 'status': 'skipped', 'reason': 'source_not_ready_or_id_mismatch'}
    writer = EvidenceWriter(directory)
    cleaned = writer.clean(source)
    encoded = json.dumps(cleaned, ensure_ascii=False, indent=2).encode('utf-8')
    copy = writer.clean(public_copy(source)).encode('utf-8')
    exported_sha = hashlib.sha256(encoded).hexdigest()
    provenance = {'case_id': case_id, 'tenant_id': tenant_id, 'store_id': store_id,
                  'final_design_id': artifact['id'], 'source_run_id': source_id,
                  'selection': 'completed_ready_image_artifact.source_run_id',
                  'database_access': 'sqlite_mode_ro_and_query_only',
                  'source_body_sha256': hashlib.sha256(row[0].encode('utf-8')).hexdigest(),
                  'exported_source_sha256': exported_sha,
                  'exported_copy_sha256': hashlib.sha256(copy).hexdigest(),
                  'source_file': 'image-source-run.json', 'copy_file': 'image-source-copy.txt',
                  'original_copy_untouched': True}
    _write_consistent(directory / 'image-source-run.json', encoded)
    _write_consistent(directory / 'image-source-copy.txt', copy)
    _write_consistent(directory / 'image-source-provenance.json', json.dumps(provenance, ensure_ascii=False, indent=2).encode('utf-8'))
    return {'case_id': case_id, 'status': 'exported', 'source_run_id': source_id,
            'source_sha256': exported_sha, 'final_design_id': artifact['id']}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', type=Path, required=True)
    args = parser.parse_args(argv)
    root = args.root.resolve()
    manifest = read_json(root / 'cases.json')
    cases = manifest.get('cases') if isinstance(manifest, dict) else manifest
    if not isinstance(cases, list):
        parser.error('root needs cases.json list')
    results = []
    for case in cases:
        try:
            results.append(export_case(root, case))
        except (OSError, ValueError, sqlite3.Error) as exc:
            results.append({'case_id': case.get('case_id'), 'status': 'error', 'error_type': type(exc).__name__})
    summary = {'database_access': 'read_only', 'cases': results}
    (root / 'source-export-summary.json').write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps(summary, ensure_ascii=True))
    return 1 if any(item['status'] == 'error' for item in results) else 0


if __name__ == '__main__':
    raise SystemExit(main())
