"""Create a read-only evidence snapshot of cold-start copy and final rendered PNGs.

No application imports, model calls, database writes, or automatic quality scores.
Only index.html and summary.json are written; independent reviews remain untouched.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
from html import escape
import json
from pathlib import Path
import re
from urllib.parse import quote

DIMENSIONS = {'product': '品牌与产品', 'world': '通用事实', 'legal': '法规与广告',
              'empathy': '用户情感', 'community': '社区氛围', 'timeliness': '近期舆论'}
REVIEW_FILES = ('independent-text-review.json', 'independent-image-review.json', 'independent-review.json')
QUALITY_FIELDS = ('quality_result', 'quality_verdict', 'verdict', 'decision', 'status')


def independent_quality(reviews):
    """Normalize only explicit human-record labels; retain all labels/conflicts."""
    labels = {'pass': 'pass', 'passed': 'pass', 'accept': 'pass', 'accepted': 'pass',
              'fail': 'fail', 'failed': 'fail', 'reject': 'fail', 'rejected': 'fail',
              'revise': 'fail', 'reselect': 'fail', 'needs_revision': 'fail',
              'not_evaluable': 'not_evaluable', 'not_reviewed': 'not_evaluable',
              'unreviewed': 'not_evaluable', 'mixed': 'mixed'}
    records = []
    for item in reviews:
        row = item['review']
        raw = {key: row[key] for key in QUALITY_FIELDS if key in row} if isinstance(row, dict) else {}
        outcomes = {labels[value.strip().lower()] for value in raw.values()
                    if isinstance(value, str) and value.strip().lower() in labels}
        if any(not isinstance(value, str) or (value.strip().lower() not in labels
               and not (key == 'status' and value.strip().lower() in ('evaluated', 'inspected', 'reviewed')))
               for key, value in raw.items()):
            outcomes.add('unknown')
        quality = ('mixed' if len(outcomes) > 1 else next(iter(outcomes)) if outcomes else 'unknown')
        # A not-evaluable record never certifies an actual inspection, even if
        # another field contradictorily calls it passed.
        inspected = ('not_evaluable' not in outcomes and
                     (bool(outcomes - {'unknown'}) or raw.get('status') in ('evaluated', 'inspected', 'reviewed')))
        records.append({'file': item['file'], 'verdict': next(iter(raw.values()), 'record_available'),
                        'raw_verdicts': raw, 'quality': quality, 'inspected': inspected})
    outcomes = {row['quality'] for row in records}
    quality = ('not_reviewed' if not outcomes else 'mixed' if len(outcomes) > 1 else next(iter(outcomes)))
    return quality, any(row['inspected'] for row in records), records


def read_json(path):
    if not path.is_file():
        return None
    try:
        return json.loads(path.read_text(encoding='utf-8-sig'))
    except (OSError, ValueError):
        return {'_read_error': 'incomplete_or_invalid_json', '_file': path.name}


def within(path, parent):
    try:
        path.resolve().relative_to(parent.resolve())
        return True
    except ValueError:
        return False


def selected_review(document, case_id):
    if not isinstance(document, (dict, list)):
        return None
    records = document.get('cases', document) if isinstance(document, dict) else document
    if isinstance(records, dict):
        if isinstance(records.get(case_id), dict):
            return records[case_id]
        if records.get('case_id', records.get('case')) == case_id:
            return records
    if isinstance(records, list):
        return next((row for row in records if isinstance(row, dict)
                     and row.get('case_id', row.get('case', row.get('id'))) == case_id), None)
    return None


def public_copy(record):
    if not isinstance(record, dict):
        return ''
    parts = [record.get('title'), record.get('intro')]
    parts.extend(block.get('text', '') for block in record.get('blocks', []) if isinstance(block, dict))
    parts.append(record.get('cta'))
    return '\n\n'.join(str(item) for item in parts if item)


def model_reviews(document, origin):
    found, seen = [], set()
    def walk(value, path):
        if isinstance(value, dict):
            if isinstance(value.get('dimensions'), dict) and ('decision' in value or 'action' in value):
                fingerprint = hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
                if fingerprint not in seen:
                    seen.add(fingerprint)
                    found.append({'file': origin, 'path': path, 'decision': value.get('decision', value.get('action')),
                                  'phase': value.get('phase'), 'render_sha256': value.get('render_sha256'),
                                  'dimensions': {k: v.get('status') for k, v in value['dimensions'].items() if isinstance(v, dict)},
                                  'creative_effectiveness': value.get('creative_effectiveness'),
                                  'revision_brief': value.get('revision_brief'), 'fingerprint': fingerprint})
            for key, item in value.items():
                if key not in {'assets', 'media', 'data_url', 'b64_json', 'image_base64'}:
                    walk(item, path + '.' + str(key))
        elif isinstance(value, list):
            for i, item in enumerate(value):
                walk(item, path + f'[{i}]')
    walk(document, '$')
    return found


def image_record(path, root, declared):
    from PIL import Image
    result = {'path': path.relative_to(root).as_posix(), 'sha256': hashlib.sha256(path.read_bytes()).hexdigest(),
              'bytes': path.stat().st_size, 'declared_by_pipeline': path.name in declared}
    try:
        with Image.open(path) as decoded:
            result['width'], result['height'] = decoded.size
            decoded.verify()
        result['decodable'] = True
    except (OSError, ValueError):
        result['decodable'] = False
    expected = declared.get(path.name, {}).get('png_sha256')
    result['declared_sha256'] = expected
    result['declared_hash_matches'] = result['sha256'] == expected if expected else None
    return result


def build_case(root, case, shared_reviews):
    identifier = case['case_id']
    if not re.fullmatch(r'[A-Za-z0-9_-]{1,100}', identifier):
        raise ValueError('Unsafe case identifier')
    directory = root / identifier
    if not within(directory, root):
        raise ValueError('Case directory escapes root')
    report = read_json(directory / 'report.json') or {}
    stages = report.get('stages') or {}
    design = read_json(directory / 'image-design.json') or {}
    initial = read_json(directory / 'text-run.json') or {}
    final_source = read_json(directory / 'image-source-run.json') or {}
    initial_id = initial.get('run_id') or (stages.get('text', {}).get('artifact') or {}).get('id')
    image_source_id = design.get('source_run_id')
    final_id = final_source.get('run_id') or final_source.get('id')
    final_matches = bool(image_source_id and final_id == image_source_id)
    copy_path = directory / 'copy.txt'
    initial_copy = copy_path.read_text(encoding='utf-8-sig') if copy_path.is_file() else public_copy(initial)
    text = public_copy(final_source) if final_matches else initial_copy
    alignment = ('final_source_exported' if final_matches else 'same_source' if image_source_id and initial_id == image_source_id
                 else 'final_source_not_exported' if image_source_id else 'no_delivered_image')
    declarations = {Path(item.get('png', '')).name: item for item in report.get('renders', []) if isinstance(item, dict)}
    renders = directory / 'renders'
    images = [image_record(path, root, declarations) for path in sorted(renders.glob('*.png'))
              if within(path, renders) and within(path, root)] if renders.is_dir() else []
    image_stage = stages.get('image') or {}
    genuinely_delivered = bool(report.get('pipeline_completed') and image_stage.get('turn_status') == 'completed'
                               and image_stage.get('ready') and (image_stage.get('artifact') or {}).get('kind') == 'design'
                               and bool(images) and len(images) == len(declarations)
                               and all(item['decodable'] and item['declared_by_pipeline'] and item['declared_hash_matches'] is not False
                                       for item in images))
    reviews = []
    for name, document in shared_reviews.items():
        selected = selected_review(document, identifier)
        if selected is not None:
            reviews.append({'file': name, 'review': selected})
    for name in (*REVIEW_FILES, 'review.json'):
        document = read_json(directory / name)
        if document:
            reviews.append({'file': identifier + '/' + name, 'review': selected_review(document, identifier) or document})
    documents = {'text-run.json': initial, 'image-design.json': design}
    if final_source:
        documents['image-source-run.json'] = final_source
    for path in sorted(directory.glob('design-work-*.json')):
        documents[path.name] = read_json(path) or {}
    automated_reviews = []
    for filename, document in documents.items():
        automated_reviews.extend(model_reviews(document, identifier + '/' + filename))
    run_records = {str(record.get('run_id') or name): record for name, record in documents.items()
                   if name in {'text-run.json', 'image-source-run.json'} and record}
    text_reselections = sum(sum(item.get('stage') == 'automatic_reselection' for item in record.get('trace', []) if isinstance(item, dict))
                            for record in run_records.values())
    image_reselection = int(bool(image_source_id and initial_id and image_source_id != initial_id))
    hard_timeout = read_json(directory / 'hard-timeout.json')
    quality, inspected, independent_verdicts = independent_quality(reviews)
    return {'case_id': identifier, 'store_name': case['store_name'], 'execution_status': report.get('execution_status', 'not_started'),
            'report_present': bool(report), 'pipeline_completed': bool(report.get('pipeline_completed')),
            'genuinely_delivered': genuinely_delivered, 'quality_is_independent_of_delivery': True,
            'stages': {name: {key: value.get(key) for key in ('state', 'turn_status', 'ready', 'error', 'message', 'expected_kind_matched')}
                       for name, value in stages.items()},
            'hard_timeout': hard_timeout, 'unconfirmed_started_operations': report.get('unconfirmed_started_operations', []),
            'copy': text, 'initial_copy': initial_copy if final_matches and text != initial_copy else None,
            'copy_origin': 'image-source-run.json' if final_matches else 'copy.txt' if copy_path.is_file() else 'text-run.json',
            'copy_image_source_alignment': alignment, 'images': images,
            'reference_media_count_excluded': len(list((directory / 'media').glob('*'))) if (directory / 'media').is_dir() else 0,
            'automatic_review_records': automated_reviews, 'independent_reviews': reviews,
            'independent_quality': quality, 'independent_inspected': inspected,
            'independent_verdicts': independent_verdicts,
            'reselection': {'confirmed_text_trace_count': text_reselections, 'confirmed_image_source_change_count': image_reselection,
                            'confirmed_minimum': text_reselections + image_reselection,
                            'count_basis': '实际automatic_reselection trace与最终图源稿变化；未知中间操作不计成完成重选'},
            'sources_changed_during_run': any(item.get('source_changed_during_invocation') for item in report.get('invocations', []))}


def html_summary(summary):
    e = lambda value: escape(str(value), quote=True)
    total_labels = {'cases': '案例', 'genuinely_delivered': '实际交付',
                    'independent_inspected': '独立已检', 'independent_passed': '独立通过',
                    'independent_failed': '独立失败', 'independent_mixed': '结论冲突 mixed',
                    'independent_unevaluated': '未评', 'independent_unknown': '结论未知'}
    totals_text = ' · '.join(f'{label}={summary.get("totals", {}).get(key, 0)}'
                             for key, label in total_labels.items()) if 'totals' in summary else ''
    sections = []
    for case in summary['cases']:
        stages = ''.join(f'<li><strong>{e(name)}</strong> · {e(value.get("turn_status"))} · ready={e(value.get("ready"))}'
                         + (f' · {e((value.get("error") or {}).get("code"))}' if isinstance(value.get('error'), dict) else '') + '</li>'
                         for name, value in case['stages'].items()) or '<li>尚未开始</li>'
        figures = ''.join(f'<figure><a href="{quote(item["path"], safe="/")}"><img src="{quote(item["path"], safe="/")}" alt="{e(case["store_name"])} 实际最终渲染"></a>'
                          f'<figcaption>{e(item["width"])} × {e(item["height"])} · SHA256 {e(item["sha256"])}<br>pipeline声明={e(item["declared_by_pipeline"])} · hash一致={e(item["declared_hash_matches"])}</figcaption></figure>'
                          for item in case['images'] if item.get('decodable')) or '<p class="empty">没有可核查的最终 PNG。中间素材不作为成图。</p>'
        automated = ''.join('<details><summary>' + e(item['phase']) + ' · ' + e(item['decision']) + ' · ' + e(item['file']) + '</summary><pre>'
                            + e(json.dumps(item, ensure_ascii=False, indent=2)) + '</pre></details>' for item in case['automatic_review_records']) or '<p>未导出有效的模型审阅记录。</p>'
        verdict_labels = {row['file']: json.dumps(row['raw_verdicts'], ensure_ascii=False) + ' → ' + row['quality']
                          for row in case['independent_verdicts']}
        independent = ''.join('<details open><summary>' + e(item['file']) + ' · ' + e(verdict_labels[item['file']]) + '</summary><pre>'
                              + e(json.dumps(item['review'], ensure_ascii=False, indent=2)) + '</pre></details>' for item in case['independent_reviews']) or '<p>尚无独立审阅，质量未判定。</p>'
        alignment = '<p class="attention">当前展示首轮原文；最终成图已换源稿，但源稿未导出，不能据此确认图文一致。</p>' if case['copy_image_source_alignment'] == 'final_source_not_exported' else ''
        initial = '<details><summary>重选前首轮原文</summary><pre>' + e(case['initial_copy']) + '</pre></details>' if case['initial_copy'] else ''
        sections.append(f'<section id="{e(case["case_id"])}"><header><h2>{e(case["store_name"])}</h2><p>{e(case["execution_status"])} · 实际交付={e(case["genuinely_delivered"])} · 独立质量={e(case["independent_quality"])}</p></header>'
                        f'<ul>{stages}</ul><p>确认重选至少 {case["reselection"]["confirmed_minimum"]} 次 · 排除 {case["reference_media_count_excluded"]} 个中间/参考素材</p>'
                        f'{alignment}<div class="work"><article><h3>实际文案</h3><pre class="copy">{e(case["copy"]) if case["copy"] else "未产出可交付文案。"}</pre>{initial}</article><div>{figures}</div></div>'
                        f'<h3>独立审阅</h3>{independent}<h3>模型六维与创意评估记录</h3>{automated}'
                        f'<details><summary>执行证据摘要</summary><pre>{e(json.dumps({k:v for k,v in case.items() if k not in {"copy","initial_copy","automatic_review_records","independent_reviews"}}, ensure_ascii=False, indent=2))}</pre></details></section>')
    return '<!doctype html><html lang="zh-CN"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>MateQuill 陌生门店盲测</title><style>' + '''
body{font:17px/1.7 system-ui,"Microsoft YaHei",sans-serif;margin:0;background:#f7f7f2;color:#242b22}main{max-width:1300px;margin:auto;padding:48px 28px}h1{font-size:36px;line-height:1.3}h2{font-size:26px}h3{font-size:19px}section{margin:36px 0;padding:28px;background:white;border:1px solid #dfe3d7;border-radius:18px}header p{font-weight:600}pre{white-space:pre-wrap;overflow-wrap:anywhere;font:15px/1.7 ui-monospace,monospace}.copy{font:19px/1.9 system-ui,sans-serif}.work{display:grid;grid-template-columns:minmax(0,1fr) minmax(0,1fr);gap:32px}figure{margin:0 0 24px}img{max-width:100%;max-height:900px;object-fit:contain;background:#f1f1ec}figcaption{font-size:13px;overflow-wrap:anywhere}details{margin:12px 0;padding:12px;background:#f6f7f3}summary{cursor:pointer}.empty,.attention{padding:16px;background:#f5f0e6}a{color:#245a3d}@media(max-width:800px){.work{grid-template-columns:1fr}main{padding:24px 14px}section{padding:18px}}
''' + '</style><main><h1>陌生门店 · 图文真实盲测</h1><p>快照时间：' + e(summary['snapshot_at']) + '</p><p>保留首次执行。下方文案未经人工润色；仅展示 renders 中实际最终 PNG。模型 ready 或审核自评分不代表独立质量通过。缺失、失败与未知操作均保留。</p><p>' + e(totals_text) + '</p><p>独立检查按门店案例去重；not_evaluable不计已检或通过；冲突标mixed，未知标签不推断通过。</p><p><a href="summary.json">下载结构化摘要</a></p>' + ''.join(sections) + '</main></html>'


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', type=Path, default=Path('output/six-dimension-blind-benchmark'))
    args = parser.parse_args(argv)
    root = args.root.resolve()
    manifest = read_json(root / 'cases.json')
    cases = manifest.get('cases', []) if isinstance(manifest, dict) else manifest
    if not isinstance(cases, list):
        parser.error('root must contain a cases.json array or object with cases array')
    shared = {name: read_json(root / name) for name in REVIEW_FILES}
    summary = {'schema_version': 1, 'snapshot_at': datetime.now(timezone.utc).isoformat(), 'root': str(root),
               'purpose': 'read_only_evidence_snapshot', 'quality_policy': '模型ready不代表独立质量通过；最终PNG缺失不算图文交付',
               'cases': [build_case(root, case, shared) for case in cases]}
    summary['totals'] = {'cases': len(summary['cases']), 'genuinely_delivered': sum(item['genuinely_delivered'] for item in summary['cases']),
                         'with_final_png': sum(bool(item['images']) for item in summary['cases']),
                         'with_independent_review_records': sum(bool(item['independent_reviews']) for item in summary['cases'])}
    summary['totals'].update({
        'independent_inspected': sum(item['independent_inspected'] for item in summary['cases']),
        'independent_passed': sum(item['independent_inspected'] and item['independent_quality'] == 'pass' for item in summary['cases']),
        'independent_failed': sum(item['independent_inspected'] and item['independent_quality'] == 'fail' for item in summary['cases']),
        'independent_mixed': sum(item['independent_quality'] == 'mixed' for item in summary['cases']),
        'independent_unevaluated': sum(not item['independent_inspected'] for item in summary['cases']),
        'independent_unknown': sum(item['independent_quality'] == 'unknown' for item in summary['cases'])})
    (root / 'summary.json').write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding='utf-8')
    (root / 'index.html').write_text(html_summary(summary), encoding='utf-8')
    print(json.dumps({'gallery': str(root / 'index.html'), 'totals': summary['totals']}, ensure_ascii=True))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
