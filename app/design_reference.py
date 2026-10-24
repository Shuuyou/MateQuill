"""Versioned design methods and permitted local composition experience."""
from fastapi import HTTPException
from .tenancy import load_body, require_context
from .tenancy.resources import list_resource_headers

METHODS = (
    {'id': 'whole_composition', 'version': '1.0', 'platforms': ['xiaohongshu','poster','douyin'],
     'method': '先确定完整的视线顺序与主体关系，再分解图像和文字；图片是场景时保留其整体性。',
     'source': 'https://jingyechen.github.io/accordion/'},
    {'id': 'editable_hierarchy', 'version': '1.0', 'platforms': ['wechat','poster','bilibili'],
     'method': '让标题、主体、解释各承担独立信息职责，按画幅重新编排，保留可编辑文字与图像。',
     'source': 'https://aclanthology.org/2025.emnlp-main.214/'},
    {'id': 'natural_photo', 'version': '1.0', 'platforms': ['moments','xiaohongshu'],
     'method': '已有现场照片优先用光线、裁切和照片顺序表达；可无字，不把宣传信息重复铺满照片。',
     'source': 'https://school.xiaohongshu.com/lesson/normal/af0fe7e97bb0433fac9da5d1df52b07f'},
)


def design_references(platform, *, limit=3):
    """Return structural methods, not copied artwork or purported outcomes."""
    context = require_context()
    methods = [{**item, 'kind': 'published_design_method'} for item in METHODS if platform in item['platforms']]
    history = []
    for row in list_resource_headers('design', limit=12):
        if row['store_id'] != context.store_id:
            continue
        try:
            body = load_body('design_style', row['id'])
        except HTTPException:
            continue
        if body.get('platform') != platform or body.get('status') != 'ready':
            continue
        try:
            # Source revocation also removes derived composition context.
            from .design_service import source_record
            source_record(body['source_run_id'])
        except Exception:
            continue
        selected = body.get('concept')
        if selected:
            history.append({'kind': 'previous_store_composition', 'design_id': body['id'],
                'visual_thesis': selected.get('visual_thesis'), 'composition': selected.get('composition'),
                'palette': [selected.get('background'), selected.get('foreground')],
                'usage': '近期已经做过的表达，用于避免机械重复；不证明已发布或传播效果。'})
        if len(history) >= limit:
            break
    return {'methods': methods, 'recent_compositions': history}
