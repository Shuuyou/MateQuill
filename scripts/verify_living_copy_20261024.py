"""Run one real, source-frozen copy case; keep failures and never replay it.

The caller supplies provider configuration through the process environment.
No secrets, customer database, simulated writer, votes or model preferences
are supplied by this script. Scene/audience are held inputs, not POI findings.
"""
from __future__ import annotations
import argparse
import asyncio
import hashlib
import json
import os
from pathlib import Path
import secrets
import sys
import time


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--code-root', type=Path, required=True)
    parser.add_argument('--case', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--content-role', action='store_true', help='Use the explicit content-model factory, recorded as a separate experiment arm')
    args = parser.parse_args()
    code = args.code_root.resolve()
    output = args.output.resolve()
    if output.exists():
        raise RuntimeError('Use a fresh case output; an actual request is never replayed')
    output.mkdir(parents=True)
    database = output/'case.sqlite3'
    os.environ['MATEQUILL_DATABASE_URL'] = 'sqlite:///'+database.as_posix()
    os.environ['ALLOW_LOCAL_SETTINGS'] = '0'
    sys.path.insert(0, str(code))
    case = json.loads(args.case.read_text(encoding='utf-8'))
    report = {'started_at': time.time(), 'input': case, 'calls_attempted': False,
              'code_root': str(code), 'audience_and_scene': 'held_input_not_autonomous_research',
              'new_images_generated': False, 'human_preference': None,
              'explicit_content_role': args.content_role}
    def save():
        (output/'report.json').write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
    save()

    async def run():
        from scripts.tenant_admin import bootstrap
        from app.tenancy import TenantContext, set_context, reset_context, register_resource
        from app.tenancy.auth import new_session, session_row, context_from_session
        from app.store_campaigns import StoreInput, StoreRepository
        from app.models import TaskInput
        from app.local_knowledge import LocalKnowledgeProvider
        from app.task_context import TaskContextProvider
        from app.llm import OpenAICompatibleLLM
        from app.daily_delivery import daily_content_delivery
        from app.workflow import AgentWorkflow
        account = bootstrap(email='copy-case-'+secrets.token_hex(8)+'@example.test',
            name='图文隔离验收', tenant_name='内容研究隔离案例', password=secrets.token_urlsafe(32))
        admin = set_context(TenantContext(account['user_id'], account['tenant_id'],
            roles=('admin',), all_stores=True, grants=(('admin', None),)))
        try:
            store = StoreRepository().save_store(StoreInput(name=case['store']['name'],
                location=case['store']['location']))
        finally:
            reset_context(admin)
        context = context_from_session(session_row(new_session(account['user_id'], account['tenant_id'], store['id'])))
        token = set_context(context)
        if args.content_role:
            from app.content_models import build_content_model
            model = build_content_model(calls=12, tokens=6000, timeout=90)
        else:
            model = OpenAICompatibleLLM(os.environ['LLM_BASE_URL'], os.environ['LLM_API_KEY'],
                os.environ['LLM_MODEL'], max_calls=12, max_tokens=6000, timeout=60)
        try:
            task = TaskInput(**case['task'], store_id=store['id'])
            provider = TaskContextProvider(LocalKnowledgeProvider(root=output/'empty-private-archive'), task)
            bundle = await provider.retrieve(task.product, task.scene)
            snapshot = bundle.model_dump(mode='json')
            (output/'retrieved-evidence.json').write_text(json.dumps(snapshot, ensure_ascii=False, indent=2), encoding='utf-8')
            report['retrieved_evidence_sha256'] = hashlib.sha256(json.dumps(snapshot, ensure_ascii=False, sort_keys=True).encode()).hexdigest()
            report['fact_count'] = len(bundle.facts)
            report['calls_attempted'] = True
            save()
            # Both variants use the same trusted text-delivery scope. This is
            # not a user-supplied flag or a claim to have generated pictures.
            with daily_content_delivery({'source': 'daily_store',
                    'content_delivery': 'text_with_optional_existing_images'}):
                result = await asyncio.wait_for(AgentWorkflow(provider, model).run(task), timeout=360)
            record = result.model_dump(mode='json')
            record['_poster_task'] = task.model_dump(mode='json')
            register_resource('run', result.run_id, body=record, store_id=store['id'], visibility='store')
            (output/'run.json').write_text(json.dumps(record, ensure_ascii=False, indent=2), encoding='utf-8')
            report.update(status=result.status, run_id=result.run_id,
                public_copy={key: record.get(key) for key in ('title','intro','blocks','cta','tags')},
                model=model.model, model_calls=model._calls, metrics=model.metrics,
                audit=record['audit'], image_count=len(result.images))
        except Exception as exc:
            report.update(status='failed', error_type=type(exc).__name__,
                error_code=getattr(exc, 'code', None), model_calls=model._calls, metrics=model.metrics)
        finally:
            reset_context(token)
    asyncio.run(run())
    report['finished_at'] = time.time()
    report['elapsed_seconds'] = round(report['finished_at']-report['started_at'], 2)
    save()
    print(json.dumps({key: report.get(key) for key in ('status','model_calls','elapsed_seconds','error_type')}, ensure_ascii=True))


if __name__ == '__main__':
    main()
