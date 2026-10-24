"""Source-bound store context for design, derived without extra forms or network IO.

Public geography and historical content directions are editorial hypotheses.
Only authorized store records and original uploaded media describe this store.
"""
from __future__ import annotations

import re
from typing import Any

from fastapi import HTTPException

from .models import TaskInput
from .tenancy import authorize_resource, list_resources, require_context
from .store_memory import collect_store_memory
from .gtm_agent.place_context import local_editorial_context
from .public_signals import public_url


def _text(value: Any, limit: int = 500) -> str:
    if not isinstance(value, str):
        return ''
    value = re.sub(r'\s+', ' ', value).strip()
    value = re.sub(r'(?<!\d)1\d{10}(?!\d)|[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}', '[联系方式]', value)
    return value[:limit]


def _rows(value, maximum=12):
    return [item for item in value[:maximum] if isinstance(item, dict)] if isinstance(value, list) else []


def collect_design_store_context(task: TaskInput | dict, source: dict, *,
                                 assets: dict | None = None, limit: int = 12) -> dict:
    """Collect a compact context under the active store's current permissions.

    ``task`` can carry the existing server place-research envelope. This function
    reuses it and authorized opportunity caches; it never calls a search/model or
    fetches an arbitrary URL. Caller keeps ordinary task/campaign validation.
    No customer demographic or historical generated story becomes a store fact.
    """
    task = task if isinstance(task, TaskInput) else TaskInput.model_validate(task)
    context = require_context()
    store_id = context.store_id
    if not store_id or (task.store_id and task.store_id != store_id):
        raise HTTPException(409, '回到这家门店后继续设计')
    store_row = authorize_resource('store', store_id)
    profile = store_row.get('body') or {}
    source = source if isinstance(source, dict) else {}
    source_id = source.get('run_id') or source.get('id')
    if source_id:
        source_row = authorize_resource('run', source_id)
        if source_row.get('store_id') != store_id:
            raise HTTPException(409, '请使用本店的内容继续设计')
        source = source_row.get('body') or {}
    assets = assets if isinstance(assets, dict) else {}
    cap = min(20, max(1, int(limit)))
    evidence: dict[str, dict] = {}
    hypotheses, signals, preferences, opportunities = [], [], [], []

    def cite(identifier, kind, quote, *, resource=None, field='', url='', recorded_at=None):
        quote = _text(quote, 600)
        if not quote:
            return ''
        item = {'id': identifier, 'source_id': identifier, 'kind': kind, 'quote': quote}
        if resource:
            item['resource'] = resource
        if field:
            item['field'] = field
        if url and public_url(url):
            item['url'] = url[:700]
        if recorded_at is not None:
            item['recorded_at'] = _text(recorded_at, 80) if isinstance(recorded_at, str) else recorded_at
        evidence[identifier] = item
        return identifier

    identity_id = cite('store:' + store_id, 'authorized_store_record',
        '；'.join(_text(profile.get(key), 220) for key in ('name', 'location') if _text(profile.get(key))),
        resource={'kind': 'store', 'id': store_id}, field='name,location', recorded_at=store_row.get('updated_at'))
    identity = {'id': store_id, 'name': _text(profile.get('name'), 120),
                'location': _text(profile.get('location'), 240), 'publisher_kind': 'store',
                'basis': 'authorized_store_record', 'evidence_ids': [identity_id] if identity_id else []}

    task_evidence_id = cite('task:reader', 'editorial_task',
        '；'.join(_text(value, 400) for value in (task.audience, task.scene, task.goal) if value),
        resource={'kind': 'run', 'id': source_id} if source_id else None,
        field='task.audience,task.scene,task.goal')
    reader_task = _text(task.scene, 800) or _text(task.goal, 400)
    if _text(task.audience):
        hypotheses.append({'audience': _text(task.audience, 300), 'basis': 'editorial_hypothesis',
                           'evidence_ids': [task_evidence_id] if task_evidence_id else []})
    configured_audience = _text(profile.get('audience'), 300)
    if configured_audience and configured_audience != task.audience:
        identifier = cite('store:audience-direction', 'store_profile_context', configured_audience,
            resource={'kind': 'store', 'id': store_id}, field='audience')
        hypotheses.append({'audience': configured_audience, 'basis': 'store_profile_context',
                           'evidence_ids': [identifier]})

    local = local_editorial_context(task)
    local_ids = []
    if local:
        for item in _rows(local.get('evidence'), 4):
            identifier = cite('local:' + str(item.get('source_id', ''))[:120], 'public_local_signal',
                item.get('excerpt') or item.get('title'), url=str(item.get('url') or ''),
                recorded_at=item.get('observed_at'))
            if identifier:
                local_ids.append(identifier)
                signals.append({'text': _text(item.get('excerpt') or item.get('title'), 400),
                                'basis': 'public_source_excerpt', 'evidence_ids': [identifier]})
        if local.get('scene'):
            reader_task = _text(local['scene'], 800)
        if local.get('audience'):
            hypotheses.insert(0, {'audience': _text(local['audience'], 300),
                'basis': 'editorial_hypothesis', 'evidence_ids': local_ids.copy()})
        opportunities.append({'kind': 'source_grounded_editorial_proposal',
            'reader_task': reader_task, 'visual_idea': _text(local.get('visual_direction'), 600),
            'evidence_ids': local_ids.copy()})

    # Recheck every cache's assignment and version. Revoked or superseded research
    # must disappear even if the opportunity body remains stored.
    cached = [row for row in list_resources('store_opportunity') if row.get('store_id') == store_id]
    cached.sort(key=lambda row: (row.get('body') or {}).get('prepared_at', 0), reverse=True)
    from .channel_workflows import _assignment
    for row in cached[:12]:
        body = row.get('body') or {}
        assignment_id = body.get('assignment_id')
        if not assignment_id or (task.assignment_id and assignment_id != task.assignment_id):
            continue
        try:
            _, assignment, campaign = _assignment(assignment_id)
        except HTTPException:
            continue
        if assignment.get('store_id') != store_id or campaign.get('version') != body.get('campaign_version'):
            continue
        result = body.get('result') or {}
        original_task = result.get('task') or {}
        if result.get('status') != 'ready' or original_task.get('product') != task.product:
            continue
        plan = result.get('opportunity') or {}
        linked = []
        for item in _rows(result.get('sources'), 12):
            if item.get('kind') != 'public_signal' or not public_url(str(item.get('url') or '')):
                continue
            identifier = cite('opportunity:' + row['id'] + ':' + str(item.get('source_id', ''))[:80],
                'public_local_signal', item.get('excerpt') or item.get('title'),
                resource={'kind': row['kind'], 'id': row['id']}, url=item['url'],
                recorded_at=item.get('observed_at'))
            if identifier:
                linked.append(identifier)
                signals.append({'text': _text(item.get('excerpt') or item.get('title'), 400),
                    'basis': 'public_source_excerpt', 'evidence_ids': [identifier]})
        opportunity_id = cite('opportunity:' + row['id'], 'editorial_opportunity',
            '；'.join(_text(plan.get(key), 350) for key in ('title', 'scene', 'visual_idea') if plan.get(key)),
            resource={'kind': row['kind'], 'id': row['id']}, field='opportunity',
            recorded_at=body.get('prepared_at'))
        if plan.get('audience'):
            hypotheses.append({'audience': _text(plan['audience'], 300), 'basis': 'editorial_hypothesis',
                               'evidence_ids': linked or ([opportunity_id] if opportunity_id else [])})
        opportunities.append({'kind': 'cached_editorial_proposal',
            'reader_task': _text(plan.get('scene'), 700), 'visual_idea': _text(plan.get('visual_idea'), 600),
            'evidence_ids': linked or ([opportunity_id] if opportunity_id else [])})
        if not reader_task and task.assignment_id == assignment_id:
            reader_task = _text(plan.get('scene'), 800)
        break  # one latest still-valid research cache is enough for a single design

    def product_keys(value):
        product = value.get('product')
        companions = value.get('ecosystem_products') or []
        values = [product] + (companions[:4] if isinstance(companions, list) else [])
        return {re.sub(r'\s+', '', name).casefold() for name in values
                if isinstance(name, str) and name.strip()}

    current_products = product_keys(task.model_dump())
    checked_runs = {}

    def matching_history_sources(item):
        matched = []
        for ref in _rows(item.get('sources'), 6):
            if ref.get('kind') != 'run' or not isinstance(ref.get('id'), str):
                continue
            identifier = ref['id']
            if identifier not in checked_runs:
                try:
                    row = authorize_resource('run', identifier)
                except HTTPException:
                    checked_runs[identifier] = None
                else:
                    body = row.get('body') or {}
                    history_task = body.get('_poster_task') or body.get('task') or {}
                    checked_runs[identifier] = (history_task if row.get('store_id') == store_id
                        and isinstance(history_task, dict) else None)
            history_task = checked_runs[identifier]
            if history_task and current_products.intersection(product_keys(history_task)):
                matched.append(ref)
        return matched

    memory = collect_store_memory(store_id, limit=cap)
    for item in _rows(memory.get('entries'), cap):
        kind = item.get('kind')
        if kind not in ('workflow_preference', 'material_observation', 'content_hypothesis'):
            continue
        references = _rows(item.get('sources'), 6)
        if kind == 'content_hypothesis':
            references = matching_history_sources(item)
            if not references:
                continue  # unrelated or revoked history must not enter evidence or prose
        identifier = cite('memory:' + str(item.get('id', ''))[:100], kind, item.get('text'),
            field='derived_memory', resource={'kind': 'derived_memory', 'store_id': store_id})
        if not identifier:
            continue
        evidence[identifier]['source_records'] = [
            {key: _text(ref.get(key), 160) for key in ('kind', 'id', 'field', 'provenance')}
            for ref in references]
        if kind == 'workflow_preference':
            preferences.append({'text': evidence[identifier]['quote'], 'event': _text(item.get('event'), 40),
                                'evidence_ids': [identifier]})
        # Material captions are observations, never proof of actual customers or
        # claims such as stock, personal sales history or product functionality.
        if kind == 'content_hypothesis':
            opportunities.append({'kind': 'historical_content_hypothesis',
                'reader_task': evidence[identifier]['quote'], 'visual_idea': '', 'evidence_ids': [identifier]})

    # Source-upload IDs are accepted only with current authorization, current
    # task membership, and a photo belonging to this store (not another store's
    # tenant-shared image). No paths, private URLs or arbitrary metadata escape.
    photo_ids, photo_observations = [], []
    declared = {item.get('id'): item for item in _rows(source.get('assets'), 32)}
    for asset_id, item in list(assets.items())[:32]:
        if not isinstance(item, dict) or asset_id not in declared:
            continue
        media_id = item.get('media_id') or (asset_id[7:] if asset_id.startswith('upload-') else '')
        if media_id not in task.media_ids or item.get('provenance') not in ('store_upload', 'user_photo', 'authorized_photo'):
            continue
        try:
            media_row = authorize_resource('media', media_id)
        except HTTPException:
            continue
        media = media_row.get('body') or {}
        if media_row.get('store_id') != store_id or media.get('kind') != 'photo':
            continue
        identifier = cite('photo:' + media_id, 'authorized_store_photo',
            (media.get('observations') or {}).get('caption') or item.get('title') or '本次任务已授权的门店照片',
            resource={'kind': 'media', 'id': media_id}, field='observations.caption',
            recorded_at=media_row.get('created_at'))
        photo_ids.append(asset_id)
        photo_observations.append({'asset_id': asset_id, 'basis': 'photo_model_observation',
                                  'evidence_ids': [identifier]})

    services = []
    # Future integrations may supply operational service declarations in the
    # authorized store body. Existing core_tasks stay content direction, rather
    # than being silently promoted into repair/discount/inventory promises.
    raw_services = profile.get('service_capabilities') or []
    for index, item in enumerate(raw_services[:6] if isinstance(raw_services, list) else []):
        name = _text(item if isinstance(item, str) else item.get('name') if isinstance(item, dict) else '', 200)
        if not name or isinstance(item, dict) and item.get('status') in ('unavailable', 'expired', 'unconfirmed'):
            continue
        identifier = cite(f'store:service:{index}', 'recorded_service_capability', name,
            resource={'kind': 'store', 'id': store_id}, field=f'service_capabilities.{index}')
        services.append({'name': name, 'basis': 'authorized_operational_record', 'evidence_ids': [identifier]})
    declared_tasks = profile.get('core_tasks') or []
    core_tasks = [_text(value, 220) for value in declared_tasks[:6] if _text(value)] if isinstance(declared_tasks, list) else []
    if not reader_task and core_tasks:
        reader_task = core_tasks[0]
    if not reader_task:
        reader_task = '围绕本次产品的一项具体使用任务组织画面'
    service_angle = '；'.join(item['name'] for item in services[:2]) if services else reader_task
    mode = 'store_photo_and_grounded_context' if photo_ids else 'grounded_local_context' if local_ids or signals else 'authorized_store_context'
    return {'schema_version': '2026-10-22.1', 'store': identity, 'context_mode': mode,
        'reader_task': reader_task, 'service_angle': service_angle, 'service_capabilities': services,
        'local_signals': signals[:cap], 'photo_asset_ids': photo_ids[:6],
        'photo_observations': photo_observations[:6], 'audience_hypotheses': hypotheses[:6],
        'creative_opportunities': opportunities[:6], 'workflow_preferences': preferences[:6],
        'core_tasks': core_tasks, 'voice': _text(profile.get('voice'), 220),
        'evidence': list(evidence.values())[:60],
        'copy_constraints': {
            'customer_data_basis': 'editorial_hypotheses_not_measured_customer_demographics',
            'photo_basis': 'authorized_upload_with_model_observation_not_customer_identity',
            'product_claim_basis': 'approved_source_facts_only',
            'experience_basis': 'do_not_invent_customer_visits_staff_testimonials_sales_or_inventory',
            'locality_execution': '让读者任务、动作和物件关系体现在主画面，不靠页脚店名或地名证明本地化',
            'material_execution': '实拍保留来源身份；没有实拍时做产品编辑设计或明确的任务示意',
            'copy_execution': '展示文字沿用审定文本；研究和工作记录只支持选题，不自动变成广告陈述',
        }}
