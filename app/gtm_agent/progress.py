"""Small public progress projection from actual execution, never model prose."""
import time

MESSAGES = {
    'research_store': '正在查门店位置和周边线索。',
    'read_catalog': '正在确认产品型号。',
    'read_product': '正在读取这款产品的资料。',
    'ground_store': '正在结合门店周边选择内容方向。',
    'prepare_opportunity': '正在准备创作切角和到店体验方案。',
    'generate_content': '正在写稿并核对产品信息。',
    'revise_content': '正在修改文案并核对结果。',
    'create_design_bundle': '正在制作这篇内容的配图。',
    'revise_design': '正在调整配图。',
    'create_poster': '正在制作海报。',
}

# Tool-level action trace for the expandable process panel. Only real work the
# user asked about earns a step — internal decision/completion checks stay out.
STEP_TITLES = {
    'research_store': '调研门店位置与周边',
    'read_catalog': '确认产品型号',
    'read_product': '读取产品资料',
    'ground_store': '结合门店确定内容方向',
    'prepare_opportunity': '准备创作切角与体验方案',
    'read_price_rules': '读取价格资料',
    'prepare_quote': '核算报价',
    'generate_content': '撰写内容并核对信息',
    'revise_content': '修订文案',
    'create_design_bundle': '制作配图',
    'revise_design': '调整配图',
    'create_poster': '制作海报',
    'remember': '记录你的偏好',
}

STAGE_LABELS = {
    'understanding': '理解需求', 'retrieval': '检索产品资料',
    'drafting': '起草内容', 'reviewing': '事实与口径核对',
    'repairing': '按审核修订', 'finalizing': '收尾',
    'materials': '整理创作素材', 'copy': '准备画面文案',
    'concepts': '构思视觉方向', 'rendering': '绘制画面',
    'layout': '版面编排', 'background_image': '绘制背景',
    'visual_review': '复核成品画面',
}


def project_progress(body, status):
    if status not in {'queued', 'running'}:
        return None
    tool = body.get('current_tool')
    if tool in MESSAGES:
        return {'stage': tool, 'message': MESSAGES[tool]}
    # Older in-flight turns have no current_tool checkpoint. A delivery scope
    # is saved immediately before the actual generation invocation.
    if body.get('delivery_scope'):
        stage = 'create_design_bundle' if body.get('artifact') and 'images' in body['delivery_scope'] else 'generate_content'
        return {'stage': stage, 'message': MESSAGES[stage]}
    return {'stage': 'planning', 'message': '正在整理资料，确定创作方向。'}


def project_process(body, status):
    """Fold observations + pipeline events into a display-only step trace.

    Steps are real actions that already happened (observations) plus the one
    in flight (current_tool); detail lines come from stage events reported
    inside the running pipeline. Nothing here is model-generated prose.
    """
    steps = []
    previous_ts = body.get('created_at')
    for item in body.get('observations') or []:
        tool = item.get('tool')
        ts = item.get('ts')
        if tool in STEP_TITLES:
            step = {'key': tool, 'title': STEP_TITLES[tool],
                    'status': 'failed' if item.get('error') else 'done'}
            started = item.get('started_at')
            anchor = started if isinstance(started, (int, float)) else previous_ts
            if isinstance(ts, (int, float)) and isinstance(anchor, (int, float)) and ts >= anchor:
                step['duration_ms'] = int((ts - anchor) * 1000)
            steps.append(step)
        if isinstance(ts, (int, float)):
            previous_ts = ts
    current = body.get('current_tool')
    if status in {'queued', 'running'}:
        if current in STEP_TITLES:
            if steps and steps[-1]['key'] == current:
                steps[-1]['status'] = 'active'
            else:
                steps.append({'key': current, 'title': STEP_TITLES[current], 'status': 'active'})
        else:
            steps.append({'key': 'planning', 'title': '整理资料，确定创作方向', 'status': 'active'})
    detail_tail = [{'stage': STAGE_LABELS.get(event.get('stage'), event.get('stage')),
                    'text': event.get('detail'), 'seq': event.get('seq')}
                   for event in (body.get('events') or []) if event.get('detail')][-6:]
    active = next((step for step in reversed(steps) if step['status'] == 'active'), None)
    if active:
        tool_started = body.get('tool_started_at')
        if isinstance(tool_started, (int, float)):
            active['elapsed_ms'] = max(0, int((time.time() - tool_started) * 1000))
        if detail_tail:
            active['details'] = detail_tail
    if not steps and status in {'queued', 'running'}:
        steps = [{'key': 'planning', 'title': '整理资料，确定创作方向', 'status': 'active'}]
    if not steps:
        return None
    started = body.get('created_at')
    ended = body.get('updated_at') if status not in {'queued', 'running'} else None
    result = {'steps': steps}
    if isinstance(started, (int, float)):
        result['elapsed_ms'] = int(((ended or time.time()) - started) * 1000)
    return result
