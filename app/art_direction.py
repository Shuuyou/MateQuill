"""Deterministic art direction, separate from canvas coordinates and copy claims.

An optional model proposes *tokens*, not executable SVG, new prices, rewritten
facts or unreviewed headlines. The compositor owns geometry and production QA.
"""
from __future__ import annotations

from collections.abc import Mapping


PALETTES = {
    'paper_ink': {'background': '#F2EFE8', 'foreground': '#172322', 'accent': '#D65537', 'muted': '#666D68'},
    'electric': {'background': '#171A22', 'foreground': '#F7F5ED', 'accent': '#DFFF64', 'muted': '#B6B9B0'},
    'cobalt': {'background': '#EDEEE8', 'foreground': '#152547', 'accent': '#315CDA', 'muted': '#616C7E'},
    'clay': {'background': '#F1E8DC', 'foreground': '#372B2C', 'accent': '#B94E34', 'muted': '#766360'},
}
TOKENS = {
    'layout': ('editorial', 'product_stage', 'split_statement'),
    'density': ('airy', 'balanced'),
    'motif': ('orbit', 'grid', 'diagonal', 'type_only'),
    'alignment': ('left', 'center'),
    'headline_scale': ('large', 'oversized'),
    'headline_variant': ('primary', 'alternate'),
    'palette_name': tuple(PALETTES),
}
FONT_ROLES = ('sans', 'serif', 'mono', 'display')

# Aspirational customer-task language; these do not claim measured performance,
# compatibility, a feature, an actual customer identity, or a purchase discount.
SCENE_DIRECTIONS = (
    (('论文', '写作', '文献', 'research', 'thesis'), 'writing',
     ('把想法', '写成下一页'), ('下一页', '留给你的想法')),
    (('设计', '创作', '绘画', '剪辑', 'design', 'creative'), 'creation',
     ('为下一次', '灵感留白'), ('让创作', '成为今天的主角')),
    (('会议', '办公', '出差', '客户', 'meeting', 'office'), 'work',
     ('下一站', '带上你的想法'), ('把重要的事', '放在眼前')),
    (('课堂', '教室', '学习', '图书馆', '课程', 'study', 'class'), 'learning',
     ('把好奇', '写进每一天'), ('下一课', '从你的想法开始')),
)


def _data(value):
    if isinstance(value, Mapping):
        return dict(value)
    if hasattr(value, 'model_dump'):
        return value.model_dump()
    return {}


def _scene(task):
    text = ' '.join(str(task.get(k, '')) for k in ('scene', 'audience', 'goal')).lower()
    for keywords, key, primary, alternate in SCENE_DIRECTIONS:
        if any(word in text for word in keywords):
            return key, {'primary': primary, 'alternate': alternate}
    return 'everyday', {'primary': ('你的日常', '值得认真展开'),
                        'alternate': ('为下一步', '留一点想象')}


def _facts(task, result):
    """Select existing evidence only. Preserve value and *all* qualifiers.

    A short label is a label, not a newly generated benefit claim. The renderer
    must keep the associated qualifications in its conditions/footnote layer.
    """
    product = task.get('product', '')
    points, seen = [], set()
    for item in result.get('facts', []):
        fact = _data(item)
        fid = fact.get('id')
        label = fact.get('label')
        if not isinstance(fid, str) or not fid or fid in seen:
            continue
        if fact.get('product') and fact['product'] != product:
            continue
        if not isinstance(label, str) or not label.strip():
            continue
        # Long evidence headings are not cropped to a potentially false claim.
        if len(label) > 24:
            continue
        seen.add(fid)
        points.append({'label': label, 'fact_id': fid,
                       'value': str(fact.get('value', '')),
                       'qualifiers': [q for q in fact.get('qualifiers', []) if isinstance(q, str)],
                       'source_id': str(fact.get('source_id', ''))})
    return points


def normalize_art_direction(proposal=None, task=None, result=None, *, brief=None) -> dict:
    """Resolve an optional structured design proposal through a closed schema.

    Unknown strings, arbitrary colors/fonts, coordinates, SVG paths, copy and
    IDs are discarded. Models may select/reorder only supplied evidence IDs.
    """
    task, result, brief = _data(task), _data(result), _data(brief)
    proposal = _data(proposal)
    scene, headlines = _scene(task)
    direction = brief.get('direction', 'minimal')
    defaults = {
        'layout': {'editorial': 'editorial', 'bold': 'split_statement'}.get(direction, 'product_stage'),
        'density': 'airy',
        'motif': {'editorial': 'grid', 'bold': 'diagonal'}.get(direction, 'orbit'),
        'alignment': 'left', 'headline_scale': 'oversized',
        'headline_variant': 'primary',
        'palette_name': {'editorial': 'paper_ink', 'bold': 'electric'}.get(direction, 'cobalt'),
    }
    normalized = {}
    for key, allowed in TOKENS.items():
        candidate = proposal.get(key)
        normalized[key] = candidate if isinstance(candidate, str) and candidate in allowed else defaults[key]
    fonts = {'headline': 'serif' if normalized['layout'] == 'editorial' else 'display',
             'body': 'sans', 'annotation': 'mono'}
    offered_fonts = proposal.get('fonts')
    if isinstance(offered_fonts, Mapping):
        for role in fonts:
            value = offered_fonts.get(role)
            if isinstance(value, str) and value in FONT_ROLES:
                fonts[role] = value
    points = _facts(task, result)
    selected = proposal.get('fact_ids')
    if isinstance(selected, list):
        by_id = {p['fact_id']: p for p in points}
        ordered = list(dict.fromkeys(fid for fid in selected if isinstance(fid, str) and fid in by_id))
        if ordered:
            points = [by_id[fid] for fid in ordered]
    normalized.update({
        'schema_version': 1,
        'scene_intent': scene,
        'headline_lines': list(headlines[normalized['headline_variant']]),
        'product_label': str(task.get('product', '')),
        'selling_points': points[:3],
        'palette': dict(PALETTES[normalized['palette_name']]),
        'fonts': fonts,
        'background_policy': 'vector_first',
        'rationale': '先建立主张、字体层级、留白与几何关系；真实产品图独立保留，完整事实条件放入脚注。',
    })
    return normalized


def plan_art_direction(task, result=None, proposal=None, *, brief=None) -> dict:
    """Build a repeatable art brief without network calls or random templates."""
    return normalize_art_direction(proposal, task, result, brief=brief)


def art_direction_options(task, result=None) -> dict:
    """Bounded vocabulary for a future LLM design planner; never an HTML tool."""
    task, result = _data(task), _data(result)
    _, headlines = _scene(task)
    return {'tokens': {key: list(value) for key, value in TOKENS.items()},
            'font_roles': list(FONT_ROLES),
            'headlines': {key: list(value) for key, value in headlines.items()},
            'fact_ids': [point['fact_id'] for point in _facts(task, result)],
            'instruction': '只选择上述设计token和fact_ids；禁止新增文案、参数、价格、客户身份或坐标。'}
