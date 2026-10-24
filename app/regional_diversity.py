"""Permission-bound editorial diversity; facts are never treated as plagiarism.

Selection compares customer tasks, local observations and visual composition,
not whole marketing posts. Optional embeddings must be actually computed;
the deterministic feature fallback is explicitly identified in every report.
"""
from __future__ import annotations

import inspect
import json
import math
import os
import re
import unicodedata
from urllib.parse import urlparse

from fastapi import HTTPException
from sqlalchemy import select

from .tenancy import authorize_resource, require_context
from .tenancy.database import db_connection, resources
from .tenancy.resources import _permitted

VERSION = 'editorial-diversity.v1'
DIMENSIONS = ('customer_task', 'local_observation', 'visual_structure', 'angle')
TASK_ALIASES = {
    'night_photography': ('夜景', '夜拍', '暗光', '夜间拍', '拍灯箱', '记录招牌', '拍招牌', '灯牌文字'),
    'file_transfer': ('传文件', '文件传输', '传照片', '互传', '跨设备复制', '跨设备剪贴板'),
    'study_notes': ('整理笔记', '课堂笔记', '学习笔记', '课程资料', '复习整理'),
    'portrait': ('人像', '拍同学', '拍朋友', '人物摄影'),
    'commute_work': ('通勤办公', '移动办公', '路上办公', '差旅办公'),
    'coffee_photo': ('拍咖啡', '咖啡取景', '咖啡摄影', '拍杯子'),
}
VISUAL_ALIASES = {
    'closeup': ('特写', '局部放大', '细节放大', '微距'),
    'comparison': ('对照', '对比', '左右两幅', '双画面'),
    'sequence': ('连拍', '时间线', '步骤', '分镜序列'),
    'typography': ('大字', '字形', '字体', '文字设计'),
    'scene': ('环境', '街景', '场景', '全景'),
}


def _value(item, key, default=''):
    return item.get(key, default) if isinstance(item, dict) else getattr(item, key, default)


def _clean(text, common_facts=()):
    text = unicodedata.normalize('NFKC', str(text or '')).casefold()
    # Remove only supplied literal values/conditions in a comparison COPY.
    # Source facts, qualifiers and the deliverable remain byte-for-byte intact.
    for fact in common_facts:
        if isinstance(fact, dict):
            snippets = [fact.get(k, '') for k in ('value', 'quote', 'condition', 'qualifier')]
            snippets += fact.get('qualifiers', []) if isinstance(fact.get('qualifiers'), list) else []
        else:
            snippets = [fact]
        for snippet in snippets:
            if isinstance(snippet, str) and len(snippet.strip()) >= 3:
                text = text.replace(unicodedata.normalize('NFKC', snippet).casefold(), ' ')
    return re.sub(r'\s+', ' ', text).strip()[:800]


def describe(item, *, common_facts=()):
    """An editorial projection, excluding parameters, IDs, customer messages."""
    return {
        'id': str(item.get('id', ''))[:160],
        'customer_task': _clean(item.get('customer_task') or item.get('payoff') or item.get('scene'), common_facts),
        'local_observation': _clean(item.get('local_observation') or item.get('opening') or item.get('rationale'), common_facts),
        'visual_structure': _clean(item.get('visual_structure') or item.get('visual_idea') or item.get('visual_direction'), common_facts),
        'angle': _clean(item.get('angle') or item.get('content_angle') or item.get('title'), common_facts),
        'mechanism': str(item.get('mechanism', ''))[:60],
        'source_id': str(item.get('source_id', ''))[:180],
        'store_id': str(item.get('store_id', ''))[:120],
        'status': str(item.get('status', ''))[:30],
        'source_version': item.get('source_version'),
    }


def _tokens(text):
    chars = re.sub(r'[\W_]+', '', text)
    return {chars[i:i+3] for i in range(max(0, len(chars)-2))}


def _jaccard(a, b):
    return len(a & b) / len(a | b) if a and b else 0.0


def _facets(text, aliases):
    return {key for key, values in aliases.items() if any(value in text for value in values)}


def _features(descriptor):
    return ({'task:' + k for k in _facets(descriptor['customer_task'], TASK_ALIASES)}
        | {'visual:' + k for k in _facets(descriptor['visual_structure'], VISUAL_ALIASES)}
        | ({'mechanism:' + descriptor['mechanism']} if descriptor['mechanism'] else set())
        | {'observation:' + t for t in _tokens(descriptor['local_observation'])})


def _cosine(a, b):
    return max(0.0, min(1.0, sum(x*y for x, y in zip(a, b))))


def _validated_vectors(vectors, size):
    if not isinstance(vectors, list) or len(vectors) != size:
        raise ValueError('embedding_count_mismatch')
    output, dimension = [], None
    for vector in vectors:
        if not isinstance(vector, (list, tuple)) or not vector or len(vector) > 16384:
            raise ValueError('invalid_embedding')
        if any(not isinstance(v, (int, float)) or isinstance(v, bool) or not math.isfinite(v) for v in vector):
            raise ValueError('invalid_embedding')
        if dimension is not None and len(vector) != dimension:
            raise ValueError('embedding_dimension_mismatch')
        dimension = len(vector)
        norm = math.sqrt(sum(v*v for v in vector))
        if not norm:
            raise ValueError('empty_embedding')
        output.append([v/norm for v in vector])
    return output


async def configured_embeddings(texts):
    """Call an explicitly configured compatible embedding service, once/batch."""
    base = os.getenv('DIVERSITY_EMBEDDING_BASE_URL', '').rstrip('/')
    model = os.getenv('DIVERSITY_EMBEDDING_MODEL', '')
    if not base or not model:
        return None
    parsed = urlparse(base)
    if (parsed.scheme not in {'http', 'https'} or not parsed.hostname or parsed.username
            or parsed.password or parsed.query or parsed.fragment):
        raise ValueError('invalid_embedding_endpoint')
    if parsed.scheme == 'http' and parsed.hostname not in {'127.0.0.1', 'localhost', '::1'}:
        # Remote credentials only travel over TLS. Loopback self-hosted models
        # may use HTTP without exposing data beyond the serving workstation.
        raise ValueError('embedding_endpoint_requires_tls')
    import httpx
    key = os.getenv('DIVERSITY_EMBEDDING_API_KEY', '')
    headers = {'Authorization': 'Bearer ' + key} if key else {}
    async with httpx.AsyncClient(timeout=10, trust_env=False) as client:
        response = await client.post(base + '/embeddings', headers=headers,
            json={'model': model, 'input': texts, 'encoding_format': 'float'})
        response.raise_for_status()
        rows = response.json().get('data', [])
    if not isinstance(rows, list) or any(not isinstance(row, dict) for row in rows):
        raise ValueError('invalid_embedding_response')
    indexes = [row.get('index') for row in rows]
    if sorted(indexes) != list(range(len(texts))):
        raise ValueError('embedding_index_mismatch')
    return [row.get('embedding') for row in sorted(rows, key=lambda row: row['index'])]


def authorized_history(task=None, *, campaign_id='', store_id='', limit=24):
    """Read only explicit shared/approved snapshots through the central gate.

    Private BODY data is excluded in SQL, even for enterprise administrators.
    No run or conversation table is scanned. Revocation is rechecked by
    authorize_resource immediately before reading each eligible snapshot.
    """
    context = require_context()
    if context.is_legacy:
        return []
    target_store = store_id or str(_value(task, 'store_id') or context.store_id or '')
    platform = str(_value(task, 'platform'))
    kinds = ('channel_opportunity', 'marketing_opportunity', 'channel_submission')
    columns = [column for column in resources.c if column.name != 'body']
    with db_connection() as connection:
        query = select(*columns).where(resources.c.tenant_id == context.tenant_id,
            resources.c.visibility.in_(('store', 'tenant')), resources.c.kind.in_(kinds))
        # Store-side generation uses only its own store or explicit tenant share.
        # HQ preparation passes campaign_id and can coordinate authorized stores.
        if target_store and not campaign_id:
            from sqlalchemy import or_
            query = query.where(or_(resources.c.store_id == target_store, resources.c.visibility == 'tenant'))
        headers = connection.execute(query.order_by(resources.c.updated_at.desc()).limit(120)).mappings().all()
    result = []
    for header in headers:
        if not _permitted(context, header, 'read'):
            continue
        try:
            row = authorize_resource(header['kind'], header['id'])
        except HTTPException as exc:
            if exc.status_code == 404:
                continue
            raise
        body = row.get('body') or {}
        if campaign_id and body.get('campaign_id') != campaign_id:
            continue
        if body.get('status') not in {'prepared', 'adopted', 'content_ready', 'approved', 'published'}:
            continue
        if row['kind'] == 'channel_submission':
            if body.get('status') not in {'approved', 'published'}:
                continue
            snapshot = body.get('content_snapshot') or {}
            if platform and snapshot.get('platform') and snapshot['platform'] != platform:
                continue
            task_data = snapshot.get('_poster_task') or {}
            concept = (((snapshot.get('strategy') or {}).get('creative_execution') or {}).get('exploration') or {}).get('selected_concept') or {}
            projection = {**concept, 'customer_task': concept.get('customer_task') or task_data.get('scene'),
                'opening': concept.get('opening') or snapshot.get('intro'), 'title': snapshot.get('title')}
        else:
            projection = body.get('plan') or body
        record = describe({**projection, 'source_id': row['kind'] + ':' + row['id'],
            'store_id': row['store_id'], 'status': body['status'], 'source_version': row['version']})
        if any(record[key] for key in DIMENSIONS):
            result.append(record)
        if len(result) >= min(max(limit, 1), 40):
            break
    return result


def diversity_context(task=None, *, campaign_id='', store_id=''):
    history = authorized_history(task, campaign_id=campaign_id, store_id=store_id)
    return {'version': VERSION, 'history': history, 'history_count': len(history),
        'instruction': '保留准确产品事实和必要条件；换具体观察、顾客任务或视觉结构，不能仅换词。默认自主选一份，备选按需查看。'}


async def select_candidates(candidates, *, history=(), preferred_id='', common_facts=(), embedder=None):
    """MMR-style one-best choice and deterministic alternative order."""
    clean = [dict(item) for item in candidates if isinstance(item, dict) and item.get('id')]
    if not clean or len({item['id'] for item in clean}) != len(clean):
        return {'status': 'unavailable', 'selected_id': None, 'rankings': [], 'reason_code': 'invalid_candidates',
            'method': 'facet_mmr', 'embedding_state': 'not_requested', 'version': VERSION}
    clean = clean[:8]
    projected = [describe(item, common_facts=common_facts) for item in clean]
    previous = [describe(item, common_facts=common_facts) for item in list(history)[:40]]
    all_rows = projected + previous
    vectors = None
    embedding_state = 'not_configured'
    texts = ['\n'.join(row[key] for key in DIMENSIONS if row[key]) for row in all_rows]
    try:
        if previous and all(texts):
            value = (embedder or configured_embeddings)(texts)
            value = await value if inspect.isawaitable(value) else value
            if value is not None:
                vectors = _validated_vectors(value, len(texts))
                embedding_state = 'computed'
    except Exception:
        # Provider response bodies may contain secrets. Record state, not strings.
        embedding_state = 'failed'
    used_features = set().union(*(_features(row) for row in previous)) if previous else set()
    rankings = []
    for index, row in enumerate(projected):
        features = _features(row)
        gain = len(features-used_features) / max(1, len(features))
        matches = []
        for pindex, old in enumerate(previous):
            a = _facets(row['customer_task'], TASK_ALIASES)
            b = _facets(old['customer_task'], TASK_ALIASES)
            task_score = max(_jaccard(_tokens(row['customer_task']), _tokens(old['customer_task'])), _jaccard(a, b))
            observation = _jaccard(_tokens(row['local_observation']), _tokens(old['local_observation']))
            visual = max(_jaccard(_tokens(row['visual_structure']), _tokens(old['visual_structure'])),
                _jaccard(_facets(row['visual_structure'], VISUAL_ALIASES), _facets(old['visual_structure'], VISUAL_ALIASES)))
            angle = _jaccard(_tokens(row['angle']), _tokens(old['angle']))
            semantic = _cosine(vectors[index], vectors[len(projected)+pindex]) if vectors is not None else None
            # A shared task with genuinely different local observation is
            # permitted. A common camera feature alone is not creative copying.
            score = .35*task_score + .35*observation + .2*visual + .1*angle
            if semantic is not None:
                score = .6*score + .4*semantic
            matches.append({'source_id': old['source_id'], 'score': round(score, 4),
                'same_task': task_score >= .75, 'observation_similarity': round(observation, 4),
                'visual_similarity': round(visual, 4)})
        redundant = max((match['score'] for match in matches), default=0.0)
        relevance = .92 if row['id'] == preferred_id else .72
        score = .65*relevance + .2*gain - .55*redundant
        rankings.append({'candidate_id': row['id'], 'score': round(score, 5),
            'coverage_gain': round(gain, 4), 'redundancy': round(redundant, 4),
            'matches': sorted(matches, key=lambda match: (-match['score'], match['source_id']))[:3]})
    rankings.sort(key=lambda row: (-row['score'], row['candidate_id']))
    return {'status': 'selected', 'selected_id': rankings[0]['candidate_id'], 'rankings': rankings,
        'history_count': len(previous), 'method': 'embedding_facet_mmr' if vectors is not None else 'facet_mmr',
        'source_ids': list(dict.fromkeys(row['source_id'] for row in previous if row['source_id'])),
        'embedding_state': embedding_state, 'version': VERSION,
        'selection_reason': '优先贴合本店任务，并为已采用内容补充不同的生活观察和画面结构。' if previous else '采用最贴合本次任务的主方案，其他切角按需查看。'}
