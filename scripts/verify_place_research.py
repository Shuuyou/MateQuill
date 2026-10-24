"""Cold-start live discovery check; no preset shop facts or generated audiences.

Example:
  python scripts/verify_place_research.py --query '某广场 城市 地址 周边' --require-token '大学'

This verifies the network evidence layer only. It does not certify that an agent
selected the right audience or that a finished marketing artifact is suitable.
"""
from __future__ import annotations

import argparse
import asyncio
from datetime import datetime
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def assess_discovery(result: dict, *, require_tokens=(), allow_cache=False) -> dict:
    from app.public_signals import public_url

    items = result.get('items') or []
    valid = []
    for item in items:
        try:
            observed = datetime.fromisoformat(str(item.get('observed_at', '')).replace('Z', '+00:00'))
            dated = observed.tzinfo is not None
        except ValueError:
            dated = False
        if (item.get('source_id') and item.get('provider') and item.get('title')
                and item.get('excerpt') and public_url(str(item.get('url', ''))) and dated):
            valid.append(item)
    text = ' '.join(str(item.get(key, '')) for item in valid for key in ('title', 'excerpt'))
    checks = {
        'found': result.get('state', result.get('status')) == 'found',
        'network_source_with_provenance': bool(valid),
        'cold_start': allow_cache or result.get('cache_hit') is False,
        'provider_attempt_recorded': bool(result.get('providers')),
        'required_public_context_found': all(token.casefold() in text.casefold() for token in require_tokens),
    }
    return {'passed': all(checks.values()), 'checks': checks, 'valid_source_count': len(valid)}


async def verify(queries, *, require_tokens=(), allow_cache=False):
    from app.place_discovery import discover_place

    reports = []
    for query in queries:
        try:
            result = await discover_place(query, use_cache=allow_cache)
            evaluation = assess_discovery(result, require_tokens=require_tokens, allow_cache=allow_cache)
            reports.append({'query': query, **evaluation, 'discovery': result})
        except Exception as exc:
            # No credentials, transport response bodies, or environment values.
            reports.append({'query': query, 'passed': False, 'error_type': type(exc).__name__})
    return {'verification_layer': 'live_discovery_only',
            'passed': bool(reports) and all(row['passed'] for row in reports), 'queries': reports}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--query', action='append', required=True, help='Public place and city to research; repeat for blind cases.')
    parser.add_argument('--require-token', action='append', default=[], help='Optional expected public context term, not an audience assertion.')
    parser.add_argument('--allow-cache', action='store_true', help='Default forces each live discovery to bypass cache.')
    parser.add_argument('--output', type=Path, help='Optional full JSON evidence report.')
    args = parser.parse_args()
    report = asyncio.run(verify(args.query, require_tokens=args.require_token, allow_cache=args.allow_cache))
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps({'verification_layer': report['verification_layer'], 'passed': report['passed'],
                      'queries': [{key: row[key] for key in ('query', 'passed', 'checks', 'valid_source_count', 'error_type') if key in row}
                                  for row in report['queries']]}, ensure_ascii=True, indent=2))
    return 0 if report['passed'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
