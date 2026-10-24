"""Read-only, permission-scoped memory derived from real workflow records.

No extra model call or persistent profile is created. Removing/restricting the
underlying resource immediately removes it from the next collection.
"""
from __future__ import annotations

from datetime import datetime, timezone
from hashlib import sha256
import re

from fastapi import HTTPException

from .tenancy import authorize_resource, list_resources, require_context


def _text(value, limit=700):
    if not isinstance(value, str):
        return ''
    value = re.sub(r'\s+', ' ', value).strip()
    # Personal contacts are unnecessary for choosing creative directions.
    value = re.sub(r'(?<!\d)1\d{10}(?!\d)|[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}', '[联系方式]', value)
    return value[:limit]


def collect_store_memory(store_id: str, *, limit: int = 30) -> dict:
    """Return traceable observations, editorial context and workflow preferences.

    A media caption is a model observation of an uploaded photo, not verified
    product knowledge or proof of customers. Task audiences remain hypotheses.
    """
    context = require_context()
    if not context.is_legacy and context.store_id != store_id:
        raise HTTPException(409, '请先切换到这家门店')
    authorize_resource('store', store_id)
    entries = {}
    cap = min(60, max(1, int(limit)))

    def add(kind, text, row, *, field, event='', provenance='workflow_record'):
        text = _text(text)
        if not text:
            return
        key = sha256((kind + '\0' + event + '\0' + text).encode()).hexdigest()[:20]
        source = {'kind': row['kind'], 'id': row['id'], 'field': field,
                  'recorded_at': row.get('created_at'), 'provenance': provenance}
        if key in entries:
            entries[key]['sources'].append(source)
            return
        entries[key] = {'id': key, 'kind': kind, 'text': text, 'event': event,
                        'sources': [source]}

    runs = [row for row in list_resources('run') if row.get('store_id') == store_id][:40]
    accessible_runs = {row['id']: row for row in runs}
    for row in runs:
        body = row.get('body') or {}
        # Generated titles, body copy and inferred audiences are never observations.
        task = body.get('_poster_task') or body.get('task') or {}
        for field in ('scene', 'audience', 'goal'):
            add('content_hypothesis', task.get(field), row, field='task.' + field)
        for asset in body.get('assets', [])[:12]:
            if isinstance(asset, dict) and asset.get('provenance') == 'store_upload':
                add('material_observation', asset.get('title'), row, field='assets.title',
                    provenance='photo_model_observation')

    for row in list_resources('strategy_feedback')[:200]:
        if row.get('store_id') != store_id:
            continue
        body = row.get('body') or {}
        run_id = body.get('run_id')
        if run_id not in accessible_runs:
            # Old feedback is useful too, but only while its source stays readable.
            try:
                source_run = authorize_resource('run', run_id)
            except HTTPException:
                continue
            if source_run.get('store_id') != store_id:
                continue
            accessible_runs[run_id] = source_run
        event = body.get('event')
        if event not in ('adopted', 'edited', 'rejected', 'exported', 'published'):
            continue
        note = _text(body.get('note'))
        selected = accessible_runs[run_id].get('body') or {}
        selected_task = selected.get('_poster_task') or selected.get('task') or {}
        topic = ' / '.join(_text(selected_task.get(field), 100) for field in ('product', 'platform')
                           if _text(selected_task.get(field), 100))
        selection = ('；内容任务：' + topic) if topic else ''
        add('workflow_preference', (note or {'adopted': '采用过一份内容', 'edited': '修改过一份内容',
            'rejected': '拒绝过一份内容', 'exported': '导出过一份内容', 'published': '记录过一次发布'}[event]) + selection,
            row, field='note' if note else 'event', event=event,
            provenance=str(body.get('source') or 'user_report'))

    # A long creative history must not crowd out the user's actual preferences.
    priority = {'workflow_preference': 0, 'material_observation': 1, 'content_hypothesis': 2}
    selected_entries = sorted(entries.values(), key=lambda entry: priority[entry['kind']])[:cap]
    return {'store_id': store_id, 'collected_at': datetime.now(timezone.utc).isoformat(),
            'entries': selected_entries, 'mode': 'read_only_derived',
            'boundary': '照片描述是待核实的视觉观察；任务受众和场景是创作上下文。反馈只说明内容使用偏好，不代表真实客群、人口统计或成交。源记录删除或权限改变后重新读取即失效。'}
