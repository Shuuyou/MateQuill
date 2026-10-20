"""Read-only checks for the semifinal recording environment.

Never creates stores, uploads material, starts generation, or publishes content.
Use --run-id to check the export of a result that was created in the application.
"""
from __future__ import annotations

import argparse
import sys
from urllib.parse import quote

import httpx


def check(base_url, frontend_url=None, run_id=None):
    failures = []
    rows = []
    with httpx.Client(base_url=base_url.rstrip('/'), timeout=30, follow_redirects=True) as client:
        def read(path, label):
            try:
                response = client.get(path)
                response.raise_for_status()
                rows.append((label, 'OK'))
                return response
            except httpx.HTTPError as exc:
                # Do not print response bodies, which can contain user material.
                status = getattr(getattr(exc, 'response', None), 'status_code', None)
                failures.append(label)
                rows.append((label, f'FAILED ({status or type(exc).__name__})'))
                return None

        read('/health', 'Backend health')
        status = read('/api/status', 'Application status')
        if status:
            payload = status.json()
            configured = payload.get('settings', {}).get('llm_configured', False)
            rows.append(('Text model configured', 'YES' if configured else 'NO'))
            if not configured:
                failures.append('Text model configured')
        catalog = read('/api/catalog?mode=local', 'Local product catalog')
        if catalog:
            payload = catalog.json()
            items = payload if isinstance(payload, list) else payload.get('products', payload.get('items', []))
            rows.append(('Catalog entries', str(len(items))))
            if not items:
                failures.append('Catalog entries')
        stores = read('/api/stores', 'Persistent store profiles')
        if stores:
            rows.append(('User-created stores', str(len(stores.json().get('stores', [])))))
        schema = read('/openapi.json', 'API contract')
        if schema:
            paths = schema.json().get('paths', {})
            expected = {
                '/api/intake': 'post', '/api/run': 'post', '/api/stores': 'post',
                '/api/campaigns/plan': 'post', '/api/stores/{store_id}': 'put',
            }
            for path, method in expected.items():
                exists = method in paths.get(path, {})
                rows.append((f'{method.upper()} {path}', 'OK' if exists else 'MISSING'))
                if not exists:
                    failures.append(path)
            for token, label in (('media', 'Media routes'), ('product', 'Product knowledge routes')):
                count = sum(token in path for path in paths)
                rows.append((label, str(count)))
        if run_id:
            read('/api/runs/' + quote(run_id, safe='') + '/export?format=md', 'Existing result export')
        if frontend_url:
            try:
                response = client.get(frontend_url)
                response.raise_for_status()
                valid = '<html' in response.text.lower()
                rows.append(('Frontend document', 'OK' if valid else 'INVALID'))
                if not valid:
                    failures.append('Frontend document')
            except httpx.HTTPError:
                rows.append(('Frontend document', 'FAILED'))
                failures.append('Frontend document')
    for label, outcome in rows:
        print(f'{label}: {outcome}')
    print(f'Read-only checks complete: {len(failures)} required checks need attention.')
    return 1 if failures else 0


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--base-url', default='http://127.0.0.1:8000')
    parser.add_argument('--frontend-url')
    parser.add_argument('--run-id')
    args = parser.parse_args()
    try:
        return check(args.base_url, args.frontend_url, args.run_id)
    except (ValueError, httpx.HTTPError) as exc:
        print('Read-only check failed: ' + type(exc).__name__)
        return 1


if __name__ == '__main__':
    sys.exit(main())
