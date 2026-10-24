"""Make one actual design from an isolated, actually generated ready run.

No source post, review decision or resulting picture is fabricated. An unknown
or failed attempt is recorded, never replayed. Use a fresh output directory.
"""
from __future__ import annotations
import argparse
import asyncio
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import sys
import time
from uuid import uuid4

INSTRUCTION = ('为这条朋友圈只准备一张配图。让人看到一个与文字相呼应的具体观察，'
    '保留真实产品素材的视角与外观，不把整段正文搬上去，不拼参数列表。'
    '没有门店实拍，不画成这家门店或学校的现场，不造顾客、路牌与相机样张。'
    '仅用已有产品素材、文字和原生图形排版，不生成新的背景或装饰图片。')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--code-root', type=Path, required=True)
    parser.add_argument('--copy-output', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--instruction-file', type=Path)
    args = parser.parse_args()
    instruction = args.instruction_file.read_text(encoding='utf-8') if args.instruction_file else INSTRUCTION
    if args.output.exists():
        raise RuntimeError('An image attempt is never replayed; inspect the original result')
    args.output.mkdir(parents=True)
    copy = json.loads((args.copy_output/'report.json').read_text(encoding='utf-8'))
    if copy.get('status') != 'ready':
        raise RuntimeError('Actual ready source required')
    database = (args.copy_output/'case.sqlite3').resolve()
    os.environ['MATEQUILL_DATABASE_URL'] = 'sqlite:///'+database.as_posix()
    os.environ['ALLOW_LOCAL_SETTINGS'] = '0'
    sys.path.insert(0, str(args.code_root.resolve()))
    identifier = uuid4().hex
    report = {'started_at': time.time(), 'source_run_id': copy['run_id'], 'design_id': identifier,
        'instruction': instruction, 'attempted_once': False,
        'execution': 'actual_enqueued_design_in_isolated_fixture',
        'source_kind': 'actual_official_product_assets_not_store_photos', 'human_preference': None}
    def save():
        (args.output/'report.json').write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
    save()

    async def run():
        # Normal API startup registers dispatch/usage tables before the first
        # database connection. Match that order in this standalone harness.
        import app.api
        from app.tenancy import set_context, reset_context, load_body
        from app.tenancy.auth import new_session, session_row, context_from_session
        from app.design_service import enqueue_design, load_design, render_document
        from app.generation_jobs import _claim, execute_claimed, job_result
        from app.poster import PosterDocument
        with sqlite3.connect(database.resolve().as_uri()+'?mode=ro', uri=True) as connection:
            row = connection.execute('SELECT tenant_id,store_id,owner_id FROM mq_resources WHERE kind=? AND id=?',
                                     ('run', copy['run_id'])).fetchone()
        if row is None:
            raise RuntimeError('Actual source resource missing')
        tenant_id, store_id, user_id = row
        context = context_from_session(session_row(new_session(user_id, tenant_id, store_id)))
        token = set_context(context)
        try:
            report['attempted_once'] = True
            save()
            job = enqueue_design(copy['run_id'], platform='moments', instruction=instruction,
                                 idempotency_key=identifier)
            work = load_body('generation_job', job['id'])
            actual_id = work['parameters']['design_id']
            report.update(design_id=actual_id, job_id=job['id'])
            save()
            claim = _claim()
            if not claim or claim['id'] != job['id']:
                raise RuntimeError('Expected the actual isolated queued job')
            await asyncio.wait_for(execute_claimed(claim), timeout=600)
            report['job'] = job_result(job['id'])
            if report['job'].get('status') != 'succeeded':
                raise RuntimeError('Actual design job did not complete')
            artifact = load_design(actual_id)
            (args.output/'design.json').write_text(json.dumps(artifact, ensure_ascii=False, indent=2), encoding='utf-8')
            report['status'] = artifact['status']
            report['artboards'] = []
            for index, board in enumerate(artifact['artboards']):
                raw = await render_document(PosterDocument.model_validate(board['document']))
                name = f'page-{index+1:02d}.png'
                (args.output/name).write_bytes(raw)
                report['artboards'].append({'path': name, 'bytes': len(raw),
                    'sha256': hashlib.sha256(raw).hexdigest(), 'review': board.get('review'),
                    'source_integrity': board.get('source_integrity'),
                    'source_preservation': board.get('source_preservation')})
        except Exception as exc:
            report.update(status='failed', error_type=type(exc).__name__, error_code=getattr(exc, 'code', None))
            if isinstance(getattr(exc, 'detail', None), dict):
                report['error_code'] = exc.detail.get('code')
            # Preserve actual checkpoint data for diagnosis without turning it
            # into a ready deliverable or reissuing an image request.
            with sqlite3.connect(database.resolve().as_uri()+'?mode=ro', uri=True) as connection:
                row = connection.execute('SELECT body FROM mq_resources WHERE kind=? AND id=?',
                                         ('design_work', report['design_id'])).fetchone()
            if row:
                (args.output/'incomplete-work.json').write_text(row[0], encoding='utf-8')
        finally:
            reset_context(token)
    try:
        asyncio.run(run())
    except Exception as exc:
        report.update(status='preflight_failed' if not report['attempted_once'] else 'failed',
                      error_type=type(exc).__name__)
    report['elapsed_seconds'] = round(time.time()-report['started_at'], 2)
    save()
    print(json.dumps({key: report.get(key) for key in ('status','elapsed_seconds','error_type','error_code')}, ensure_ascii=True))


if __name__ == '__main__':
    main()
