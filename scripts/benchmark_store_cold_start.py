"""One isolated, unedited store-name-to-copy-to-image GTM baseline per process.

Model configuration comes exclusively from the invoking environment. This script
does not read .env files, another service's environment, cookies or credentials.
Resume never replays an attempted turn or an unconfirmed paid image request.
"""
from __future__ import annotations

import argparse
import asyncio
import base64
from contextlib import contextmanager
from datetime import datetime, timezone
import hashlib
import json
import logging
import os
from pathlib import Path
import re
import secrets
import sys
import time
import threading
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
SECRET_FIELD = re.compile(r'(^|_)(api_key|password|secret|authorization|access_token|refresh_token|session_cookie|session_hash)$', re.I)


def timestamp():
    return datetime.now(timezone.utc).isoformat()


class EvidenceWriter:
    def __init__(self, directory):
        self.directory = Path(directory)
        self.private_values = [value for key, value in os.environ.items()
                               if re.search(r'KEY|TOKEN|PASSWORD|SECRET|AUTHORIZATION', key, re.I) and len(value) >= 8]

    def asset(self, raw, mime):
        digest = hashlib.sha256(raw).hexdigest()
        suffix = {'image/png': '.png', 'image/jpeg': '.jpg', 'image/webp': '.webp',
                  'image/svg+xml': '.svg', 'audio/mpeg': '.mp3'}.get(mime, '.bin')
        relative = Path('media') / (digest + suffix)
        path = self.directory / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        if not path.exists():
            path.write_bytes(raw)
        return {'external_asset': relative.as_posix(), 'mime': mime, 'sha256': digest, 'bytes': len(raw)}

    def clean(self, value, field=''):
        if SECRET_FIELD.search(field):
            return '[redacted]'
        if isinstance(value, dict):
            return {str(key): self.clean(item, str(key)) for key, item in value.items()}
        if isinstance(value, (list, tuple)):
            return [self.clean(item) for item in value]
        if isinstance(value, bytes):
            return self.asset(value, 'application/octet-stream')
        if not isinstance(value, str):
            return value
        if value.startswith('data:') and ';base64,' in value:
            header, payload = value.split(';base64,', 1)
            try:
                return self.asset(base64.b64decode(payload, validate=True), header[5:])
            except ValueError:
                return {'invalid_data_url': True, 'characters': len(value)}
        if field in {'b64_json', 'base64', 'image_base64'} and len(value) > 1000:
            try:
                return self.asset(base64.b64decode(value, validate=True), 'application/octet-stream')
            except ValueError:
                pass
        if '<svg' in value[:300] and len(value) > 2000:
            return self.asset(value.encode(), 'image/svg+xml')
        for secret in self.private_values:
            value = value.replace(secret, '[redacted]')
        if value.startswith(('https://', 'http://')):
            try:
                url = urlsplit(value)
                query = [(key, '[redacted]' if re.search(r'token|secret|signature|credential|api.?key|authorization', key, re.I) else item)
                         for key, item in parse_qsl(url.query, keep_blank_values=True)]
                host = url.netloc.rsplit('@', 1)[-1]
                value = urlunsplit((url.scheme, host, url.path, urlencode(query), url.fragment))
            except ValueError:
                pass
        return value

    def json(self, name, value):
        path = self.directory / name
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_name(path.name + '.' + uuid4().hex + '.tmp')
        temporary.write_text(json.dumps(self.clean(value), ensure_ascii=False, indent=2, default=str), encoding='utf-8')
        # A Windows reader/indexer may briefly hold the destination without
        # delete sharing. Retry only publishing the identical local snapshot,
        # never the application turn or any provider request.
        for attempt in range(5):
            try:
                os.replace(temporary, path)
                return
            except PermissionError:
                if attempt == 4:
                    raise
                time.sleep(.04 * (2 ** attempt))

    def event(self, **value):
        event = self.clean({'at': timestamp(), **value})
        line = json.dumps(event, ensure_ascii=True)
        with (self.directory / 'events.jsonl').open('a', encoding='utf-8') as handle:
            handle.write(line + '\n')
            handle.flush()
        print(line, flush=True)


@contextmanager
def exclusive_case(directory):
    handle = (directory / '.benchmark.lock').open('a+b')
    try:
        handle.seek(0)
        if os.name == 'nt':
            import msvcrt
            if handle.read(1) == b'':
                handle.write(b'0')
                handle.flush()
            handle.seek(0)
            msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        yield
    finally:
        handle.close()


def source_snapshot(baseline):
    reference = json.loads(baseline.read_text(encoding='utf-8')) if baseline.is_file() else {}
    hashes = {}
    for name in reference.get('source_hashes', {}):
        path = (ROOT / name).resolve()
        if ROOT not in path.parents:
            continue
        hashes[name] = hashlib.sha256(path.read_bytes()).hexdigest() if path.is_file() else None
    return {'at': timestamp(), 'reference_file': str(baseline), 'source_hashes': hashes,
            'matches_reference': bool(hashes) and hashes == reference.get('source_hashes'),
            'benchmark_script_sha256': hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
            'reference_head': reference.get('head')}


def database_url(raw, directory):
    if raw and raw.startswith('sqlite:///'):
        raw = raw[len('sqlite:///'):]
    elif raw and '://' in raw:
        raise ValueError('Benchmark database must be an isolated SQLite file')
    path = Path(raw).expanduser().resolve() if raw else directory / 'benchmark.sqlite3'
    if str(path) == ':memory:' or path.name == ':memory:':
        raise ValueError('Benchmark requires a durable SQLite file for safe resume')
    path.parent.mkdir(parents=True, exist_ok=True)
    return 'sqlite:///' + path.as_posix(), path


def unconfirmed_operations(value, path=''):
    found = []
    if isinstance(value, dict):
        if value.get('status') == 'started':
            found.append(path)
        for key, item in value.items():
            found.extend(unconfirmed_operations(item, path + '.' + str(key)))
    elif isinstance(value, list):
        for index, item in enumerate(value):
            found.extend(unconfirmed_operations(item, path + f'[{index}]'))
    return found


class Benchmark:
    def __init__(self, args, directory, writer, report):
        self.args, self.directory, self.writer, self.report = args, directory, writer, report

    def save(self):
        self.report['updated_at'] = timestamp()
        self.writer.json('report.json', self.report)

    def snapshot(self, stage):
        from app.gtm_agent.repository import TURN, get_turn, get_conversation
        from app.tenancy import authorize_resource, list_resources
        identifier = stage.get('turn_id')
        if not identifier:
            return None
        projected = get_turn(self.report['conversation_id'], identifier)
        body = authorize_resource(TURN, identifier)['body']
        self.writer.json(stage['name'] + '-turn.json', body)
        stage.update(turn_status=projected['status'], message=projected.get('message'),
                     questions=projected.get('questions'), error=projected.get('error'),
                     tools=[row.get('tool') for row in body.get('observations', [])],
                     artifact=body.get('artifact'))
        reference = stage.get('artifact')
        artifact = authorize_resource(reference['kind'], reference['id'])['body'] if reference else None
        if artifact:
            stage['artifact_status'] = artifact.get('status')
            stage['actual_platform'] = artifact.get('platform') or (artifact.get('_poster_task') or {}).get('platform')
            self.writer.json(stage['name'] + '-' + reference['kind'] + '.json', artifact)
            if reference['kind'] == 'run':
                copy = '\n\n'.join(str(part) for part in [artifact.get('title'), artifact.get('intro'),
                    *[row.get('text', '') for row in artifact.get('blocks', [])], artifact.get('cta')]
                    if part)
                (self.directory / 'copy.txt').write_text(self.writer.clean(copy), encoding='utf-8')
        started = []
        work_files = []
        for row in list_resources('design_work'):
            work = row.get('body') or {}
            filename = 'design-work-' + row['id'] + '.json'
            self.writer.json(filename, work)
            work_files.append(filename)
            started += [row['id'] + path for path in unconfirmed_operations(work)]
        conversation = get_conversation(self.report['conversation_id'])
        self.writer.json('conversation.json', conversation)
        self.writer.json('provenance.json', {
            'case_id': self.report['case_id'], 'models': self.report.get('models'),
            'configuration': self.report.get('configuration'), 'invocations': self.report.get('invocations'),
            'stages': self.report['stages'], 'store_research': conversation.get('store_research'),
            'current_artifact_provenance': (artifact or {}).get('plan', {}).get('provenance'),
            'product_sources': (artifact or {}).get('sources'), 'design_work_files': work_files,
            'unconfirmed_started_operations': started,
        })
        self.report['unconfirmed_started_operations'] = started
        stage['ready'] = projected['status'] == 'completed' and artifact is not None and artifact.get('status') == 'ready'
        self.save()
        return projected

    async def stage(self, name, prompt, *, expected_kind):
        from app.gtm_agent.jobs import enqueue_turn, claim_next, execute_claimed
        from app.gtm_agent.models import TurnInput
        from app.gtm_agent.repository import get_conversation
        stage = self.report['stages'].setdefault(name, {'name': name, 'prompt': prompt, 'state': 'pending'})
        if stage.get('turn_id'):
            result = self.snapshot(stage)
            if result['status'] == 'completed' and stage.get('ready'):
                stage['state'] = 'completed'
                self.save()
                return stage.get('artifact', {}).get('kind') == expected_kind
            # Never reclaim/replay attempted work, including a stale lease or
            # an uncertain paid request. An operator must inspect its evidence.
            stage['resume_blocked'] = True
            self.save()
            self.writer.event(event='resume_blocked', stage=name, status=result['status'],
                              unconfirmed_started_operations=self.report.get('unconfirmed_started_operations', []))
            return False
        if self.args.resume and name == 'text':
            self.writer.event(event='resume_blocked', stage=name, reason='no_completed_preceding_stage')
            return False
        if not os.environ.get('LLM_API_KEY'):
            raise ValueError('LLM_API_KEY must be supplied by the invoking environment')
        conversation = get_conversation(self.report['conversation_id'])
        queued = enqueue_turn(conversation['id'], TurnInput(text=prompt, client_turn_id=uuid4().hex,
                                                          expected_version=conversation['version']))
        stage.update(turn_id=queued['id'], state='attempted', started_at=timestamp())
        self.save()  # Durable marker before any external model/image execution.
        claim = claim_next()
        if claim is None or claim['id'] != queued['id']:
            raise RuntimeError('Isolated database did not claim the exact enqueued benchmark turn')
        self.writer.event(event='stage_started', stage=name, turn_id=queued['id'])
        operation = asyncio.create_task(execute_claimed(claim))
        previous = None
        try:
            while not operation.done():
                await asyncio.wait({operation}, timeout=2)
                status = self.snapshot(stage)
                progress = (status['status'], tuple(stage['tools']))
                if progress != previous:
                    self.writer.event(event='stage_progress', stage=name, status=status['status'], tools=stage['tools'])
                    previous = progress
            await operation
        finally:
            if not operation.done():
                operation.cancel()
                await asyncio.gather(operation, return_exceptions=True)
            self.snapshot(stage)
        stage['finished_at'] = timestamp()
        stage['state'] = 'completed' if stage.get('ready') else 'stopped'
        actual_kind = stage.get('artifact', {}).get('kind') if stage.get('artifact') else None
        stage['expected_kind'] = expected_kind
        stage['expected_kind_matched'] = actual_kind == expected_kind
        self.save()
        self.writer.event(event='stage_finished', stage=name, status=stage.get('turn_status'),
                          ready=stage.get('ready'), artifact_kind=actual_kind)
        return bool(stage.get('ready') and actual_kind == expected_kind)

    async def render(self):
        from app.design import render_design_svg
        from app.design_service import artboard, render_document
        from app.tenancy import authorize_resource
        reference = self.report['stages']['image']['artifact']
        record = authorize_resource('design', reference['id'])['body']
        exports = []
        render_dir = self.directory / 'renders'
        render_dir.mkdir(parents=True, exist_ok=True)
        for index, item in enumerate(record.get('artboards', [])):
            document = artboard(reference['id'], item['id'])
            svg = await asyncio.to_thread(render_design_svg, document,
                                          lambda aid: document.assets.get(aid, {}).get('data_url', ''))
            stem = f'artboard-{index+1:02d}'
            (render_dir / (stem + '.svg')).write_text(svg, encoding='utf-8')
            png = await render_document(document)
            (render_dir / (stem + '.png')).write_bytes(png)
            from PIL import Image
            import io
            with Image.open(io.BytesIO(png)) as decoded:
                decoded.verify()
            exports.append({'board_id': item['id'], 'svg': 'renders/' + stem + '.svg', 'png': 'renders/' + stem + '.png',
                            'width': document.width, 'height': document.height,
                            'png_sha256': hashlib.sha256(png).hexdigest()})
            self.report['renders'] = exports
            self.save()
            self.writer.event(event='render_saved', board_id=item['id'], png=stem + '.png')
        return bool(exports)

    async def run(self):
        name = self.args.store_name
        if not await self.stage('text', f'我是{name}店员，请为华为Mate80写朋友圈文案', expected_kind='run'):
            return False
        if not await self.stage('image', '请为这条文案制作一张朋友圈成品配图', expected_kind='design'):
            return False
        return await self.render()


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--case-id', required=True)
    parser.add_argument('--store-name', required=True)
    parser.add_argument('--output-dir', type=Path, required=True)
    parser.add_argument('--timeout-seconds', type=int, default=900, help='Hard per-case wall-clock budget including rendering (60–1800 seconds); no attempted request is replayed.')
    parser.add_argument('--database', help='Optional new SQLite file path or sqlite:/// URL; never a shared production database.')
    parser.add_argument('--resume', action='store_true', help='Continue only after a database-confirmed completed stage; never replay an attempted turn.')
    parser.add_argument('--baseline', type=Path, default=ROOT / 'output/store-blind-benchmark/baseline.json')
    args = parser.parse_args(argv)
    if not 60 <= args.timeout_seconds <= 1800:
        parser.error('timeout-seconds must be between 60 and 1800')
    if not re.fullmatch(r'[A-Za-z0-9_-]{1,100}', args.case_id):
        parser.error('case-id must contain 1–100 ASCII letters, digits, underscores or hyphens')
    if not args.store_name.strip() or len(args.store_name) > 500:
        parser.error('store-name must contain 1–500 characters')
    directory = args.output_dir.expanduser().resolve()
    directory.mkdir(parents=True, exist_ok=True)
    writer = EvidenceWriter(directory)
    report_file = directory / 'report.json'
    with exclusive_case(directory):
        if args.resume:
            if not report_file.is_file():
                raise ValueError('Resume requires an existing benchmark report')
            report = json.loads(report_file.read_text(encoding='utf-8'))
            if report['case_id'] != args.case_id or report['store_name'] != args.store_name:
                raise ValueError('Resume case-id and store-name must match the original unedited benchmark')
            url, database = database_url(args.database or report['database_file'], directory)
            if str(database) != report['database_file'] or not database.is_file():
                raise ValueError('Resume requires the exact original SQLite database')
        else:
            url, database = database_url(args.database, directory)
            if report_file.exists() or database.exists():
                raise ValueError('Fresh benchmark requires a new report and new SQLite file; use another case directory')
            report = {'version': 1, 'case_id': args.case_id, 'store_name': args.store_name,
                      'database_file': str(database), 'started_at': timestamp(), 'stages': {},
                      'protocol': {'input': 'store_name_only', 'profile': 'generic_my_store',
                                   'retry_policy': 'never_replay_attempted_turn', 'config': 'environment_only'},
                      'pipeline_completed': False, 'local_relevance_and_visual_quality': 'not_evaluated'}
        report['timeout_seconds'] = args.timeout_seconds
        invocation = {'pid': os.getpid(), 'resume': args.resume, 'start_sources': source_snapshot(args.baseline.resolve())}
        report.setdefault('invocations', []).append(invocation)
        writer.json('report.json', report)
        os.environ['MATEQUILL_DATABASE_URL'] = url
        logging.basicConfig(level=logging.WARNING)
        logging.getLogger('httpx').setLevel(logging.WARNING)
        logging.getLogger('httpcore').setLevel(logging.WARNING)
        # Register application resource/dispatch schemas without starting any
        # background workers or reading another process's configuration.
        import app.api
        from app.gtm_agent import jobs
        from app.tenancy.database import clear_engine_cache
        from app.place_discovery import _cache as places
        from app.public_signals import _cache as signals
        from app.tenancy import set_context, reset_context
        from app.tenancy.auth import new_session, context_from_session, session_row
        from app.store_campaigns import StoreInput, StoreRepository
        from app.gtm_agent.repository import create_conversation
        clear_engine_cache()
        places.clear()
        signals.clear()
        invocation['caches_cleared'] = ['place_discovery', 'public_signals']
        report['models'] = {key: os.environ.get(key, '') for key in ('LLM_MODEL', 'IMAGE_MODEL', 'IMAGE_REVIEW_MODEL')}
        report['configuration'] = {'llm_configured': bool(os.environ.get('LLM_API_KEY')),
                                   'image_configured': bool(os.environ.get('IMAGE_API_KEY')),
                                   'visual_reviewer_configured': bool(os.environ.get('IMAGE_REVIEW_MODEL'))}
        if not args.resume:
            from scripts.tenant_admin import bootstrap
            account = bootstrap(email=f'cold-start-{args.case_id}-{uuid4().hex}@example.test',
                                name='门店冷启动盲测', tenant_name='Cold start ' + args.case_id,
                                password=secrets.token_urlsafe(32))
            token = set_context(context_from_session(session_row(new_session(account['user_id'], account['tenant_id']))))
            try:
                store = StoreRepository().save_store(StoreInput(name='我的门店'))
            finally:
                reset_context(token)
            report.update(account=account, store_id=store['id'], generic_store_profile=store)
        account = report['account']
        context = context_from_session(session_row(new_session(account['user_id'], account['tenant_id'], report['store_id'])))
        if context is None:
            raise RuntimeError('Benchmark identity is no longer authorized')
        token = set_context(context)
        runner = Benchmark(args, directory, writer, report)
        def emergency_stop():
            # A stuck synchronous SDK/default executor can outlive cancellation.
            # Write a separate marker (never race/overwrite the latest snapshot),
            # then exit without replaying or claiming an unknown provider result.
            writer.json('hard-timeout.json', {
                'case_id': args.case_id, 'stopped_at': timestamp(),
                'reason': 'wall_clock_budget_exceeded', 'timeout_seconds': args.timeout_seconds,
                'retry_policy': 'never_replay_attempted_turn',
                'last_snapshot': 'report.json', 'provider_completion': 'unconfirmed'})
            os._exit(124)
        watchdog = threading.Timer(args.timeout_seconds + 15, emergency_stop)
        watchdog.daemon = True
        watchdog.start()
        try:
            if not args.resume:
                report['conversation_id'] = create_conversation(title='门店冷启动盲测')['id']
            runner.save()
            writer.event(event='case_started', case_id=args.case_id, resume=args.resume,
                         baseline_matches=invocation['start_sources']['matches_reference'])
            async def bounded_run():
                async with asyncio.timeout(args.timeout_seconds):
                    return await runner.run()
            success = asyncio.run(bounded_run())
            report['pipeline_completed'] = success
            report['execution_status'] = 'completed' if success else 'stopped'
            return_code = 0 if success else 1
        except BaseException as exc:
            report.update(execution_status='interrupted' if isinstance(exc, (KeyboardInterrupt, asyncio.CancelledError)) else 'error',
                          exception_type=type(exc).__name__)
            writer.event(event='case_stopped', error_type=type(exc).__name__)
            return_code = 130 if isinstance(exc, (KeyboardInterrupt, asyncio.CancelledError)) else 1
        finally:
            watchdog.cancel()
            invocation['end_sources'] = source_snapshot(args.baseline.resolve())
            invocation['source_changed_during_invocation'] = invocation['start_sources']['source_hashes'] != invocation['end_sources']['source_hashes']
            invocation['runner_changed_during_invocation'] = invocation['start_sources']['benchmark_script_sha256'] != invocation['end_sources']['benchmark_script_sha256']
            report['finished_at'] = timestamp()
            runner.save()
            provenance_file = directory / 'provenance.json'
            if provenance_file.is_file():
                provenance = json.loads(provenance_file.read_text(encoding='utf-8'))
                provenance['invocations'] = report['invocations']
                writer.json('provenance.json', provenance)
            reset_context(token)
        writer.event(event='case_finished', case_id=args.case_id, status=report['execution_status'],
                     pipeline_completed=report['pipeline_completed'],
                     source_changed=invocation['source_changed_during_invocation'])
        return return_code


if __name__ == '__main__':
    try:
        raise SystemExit(main())
    except Exception as exc:
        # Keep exceptional startup/configuration failures credential-free.
        print(json.dumps({'event': 'startup_failed', 'error_type': type(exc).__name__}), flush=True)
        raise SystemExit(1) from None
