"""Read-only source export must preserve trial artifacts and tenant boundaries."""
import hashlib
import json
from pathlib import Path
import sqlite3

from scripts.export_blind_design_sources import export_case


def fixture(tmp_path, *, tenant='tenant-a', source_ready=True, image_ready=True):
    case = {'case_id': 'example', 'store_name': 'Example'}
    directory = tmp_path / 'example'
    directory.mkdir()
    database = directory / 'benchmark.sqlite3'
    source = {'run_id': 'final-source', 'status': 'ready' if source_ready else 'blocked',
              'title': 'Final source copy', 'blocks': [{'text': 'An actual paragraph'}],
              'api_key': 'DO_NOT_EXPORT'}
    with sqlite3.connect(database) as connection:
        connection.execute('CREATE TABLE mq_resources (kind TEXT, id TEXT, tenant_id TEXT, store_id TEXT, owner_id TEXT, visibility TEXT, body TEXT)')
        connection.execute('INSERT INTO mq_resources VALUES (?,?,?,?,?,?,?)',
                           ('run', 'final-source', tenant, 'store-a', 'user-a', 'private', json.dumps(source)))
    report = {'account': {'tenant_id': 'tenant-a', 'user_id': 'user-a'}, 'store_id': 'store-a',
              'database_file': str(database.resolve()),
              'stages': {'image': {'ready': image_ready, 'turn_status': 'completed',
                                   'artifact': {'kind': 'design', 'id': 'final-design'}}}}
    (directory / 'report.json').write_text(json.dumps(report), encoding='utf-8')
    (directory / 'image-design.json').write_text(json.dumps({'id': 'final-design', 'status': 'ready', 'source_run_id': 'final-source'}), encoding='utf-8')
    (directory / 'copy.txt').write_text('First attempt stays untouched', encoding='utf-8')
    return case, directory, database


def test_exports_final_source_idempotently_without_mutating_database_or_original(tmp_path):
    case, directory, database = fixture(tmp_path)
    originals = {name: (directory / name).read_bytes() for name in ('copy.txt', 'report.json', 'image-design.json', 'benchmark.sqlite3')}
    first = export_case(tmp_path, case)
    assert first['status'] == 'exported'
    assert export_case(tmp_path, case) == first
    for name, original in originals.items():
        assert (directory / name).read_bytes() == original
    source = json.loads((directory / 'image-source-run.json').read_text(encoding='utf-8'))
    assert source['run_id'] == 'final-source' and source['api_key'] == '[redacted]'
    assert (directory / 'image-source-copy.txt').read_text(encoding='utf-8') == 'Final source copy\n\nAn actual paragraph'
    provenance = json.loads((directory / 'image-source-provenance.json').read_text(encoding='utf-8'))
    assert provenance['source_run_id'] == 'final-source'
    assert provenance['exported_source_sha256'] == hashlib.sha256((directory / 'image-source-run.json').read_bytes()).hexdigest()


def test_cross_tenant_source_is_not_exported(tmp_path):
    case, directory, database = fixture(tmp_path, tenant='tenant-b')
    result = export_case(tmp_path, case)
    assert result['status'] == 'skipped' and result['reason'] == 'source_not_in_authorized_scope'
    assert not (directory / 'image-source-run.json').exists()


def test_intermediate_run_is_not_exported_without_ready_final_image(tmp_path):
    case, directory, database = fixture(tmp_path, image_ready=False)
    result = export_case(tmp_path, case)
    assert result['reason'] == 'no_ready_final_image_artifact'
    assert not (directory / 'image-source-run.json').exists()


def test_cross_store_source_is_not_exported(tmp_path):
    case, directory, database = fixture(tmp_path)
    with sqlite3.connect(database) as connection:
        connection.execute('UPDATE mq_resources SET store_id = ?', ('other-store',))
    assert export_case(tmp_path, case)['reason'] == 'source_not_in_authorized_scope'
    assert not (directory / 'image-source-run.json').exists()
