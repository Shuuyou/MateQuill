"""Re-index verified local official snapshots; no fetches or existing-run edits.

Dry run by default. --apply archives each previous catalog by its content hash
and atomically writes a new extraction version. Original capture times stay put.
"""
from __future__ import annotations

import argparse
from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import sys
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from app.facts import extract_product_facts
from app.official_catalog import extract_specification

VERSION = '2026-10-23.source-fields-v2'
CATALOGS = ('official_products.json', 'ecosystem_products.json')


def reextract_catalog(document, *, root=ROOT):
    result = deepcopy(document)
    changes = []
    for product in result['products']:
        source = next((s for s in product.get('sources', []) if s.get('role') == 'spec'), None)
        if not source or not source.get('local_path'):
            raise ValueError('Missing original specification snapshot')
        url = product.get('spec_url', '')
        parsed = urlsplit(url)
        if (parsed.scheme != 'https' or parsed.hostname != 'consumer.huawei.com'
                or parsed.username or parsed.password or parsed.query or parsed.fragment
                or source.get('url') != url):
            raise ValueError('Expected the recorded official specification URL')
        path = (root / source['local_path']).resolve()
        if not path.is_relative_to(root.resolve()) or not path.is_file():
            raise ValueError('Specification snapshot must exist inside this workspace')
        raw = path.read_bytes()
        raw_hash = hashlib.sha256(raw).hexdigest()
        if raw_hash != source.get('sha256'):
            raise ValueError('Original specification snapshot hash mismatch')
        extracted = extract_specification(raw.decode('utf-8', errors='replace'), product['name'])
        original_text = product.get('text', '')
        if not original_text or extracted['text'] != original_text:
            raise ValueError('Source text changed; use ordinary source ingestion for review')
        source_id = 'official-' + hashlib.sha256(url.encode()).hexdigest()[:16]
        facts = extract_product_facts(original_text, product['name'], source_id, max_facts=100)
        if not facts or any(f.value not in f.quote or f.quote not in original_text for f in facts):
            raise ValueError('Re-extracted facts must remain exact original source slices')
        records = [dict(f.model_dump(), source_url=url) for f in facts]
        before = product.get('facts', [])
        changed = before != records
        if changed:
            product['facts'] = records
            product['fact_extraction'] = {
                'version': VERSION,
                'reextracted_at': datetime.now(timezone.utc).isoformat(),
                'source_sha256': raw_hash,
                'source_text_sha256': hashlib.sha256(original_text.encode()).hexdigest(),
                'previous_facts_sha256': hashlib.sha256(json.dumps(before, ensure_ascii=False,
                    sort_keys=True).encode()).hexdigest(),
                'capture_time_unchanged': True,
            }
        changes.append({'product': product['name'], 'changed': changed,
            'previous_count': len(before), 'new_count': len(records),
            'new_fact_ids': sorted({f['id'] for f in records} - {f['id'] for f in before}),
            'retired_fact_ids': sorted({f['id'] for f in before} - {f['id'] for f in records})})
    return result, changes


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--apply', action='store_true')
    args = parser.parse_args()
    pending = []
    summary = {'version': VERSION, 'applied': args.apply, 'catalogs': []}
    # Verify every source before mutating either catalog.
    for name in CATALOGS:
        path = ROOT / 'data' / name
        original = path.read_bytes()
        updated, changes = reextract_catalog(json.loads(original))
        summary['catalogs'].append({'name': name, 'products': changes})
        if any(row['changed'] for row in changes):
            pending.append((path, original, updated))
    if args.apply:
        versions = ROOT / 'data' / 'fact_versions'
        versions.mkdir(exist_ok=True)
        for path, original, updated in pending:
            archive = versions / f'{path.stem}.{hashlib.sha256(original).hexdigest()}.json'
            if archive.exists() and archive.read_bytes() != original:
                raise ValueError('Existing immutable catalog archive differs')
            if not archive.exists():
                archive.write_bytes(original)
            temporary = path.with_suffix('.json.reextracting')
            temporary.write_text(json.dumps(updated, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
            temporary.replace(path)
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
