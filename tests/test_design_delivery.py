"""Design delivery uses real tenant scopes and the shared recoverable queue."""
import asyncio
import json
from uuid import uuid4

import pytest
from fastapi import HTTPException
from fastapi.responses import JSONResponse

from app import design_service as design, generation_jobs as jobs
from app.models import TaskInput
from app.tenancy import save_body, load_body
from test_generation_jobs_durability import identities, acting, state, expire

pytestmark = pytest.mark.real_tenancy


def source(owner):
    identifier = uuid4().hex
    task = TaskInput(product='HUAWEI MateBook Pro', mode='demo', store_id=owner['store']['id'])
    save_body('run', identifier, {'run_id': identifier, 'status': 'ready', 'title': '把想法写下来',
        'intro': '一个具体的学习任务', 'blocks': [{'text': '从眼前这一页开始。', 'fact_ids': []}],
        'assets': [], 'images': [], 'facts': [], '_poster_task': task.model_dump()})
    return identifier


def test_design_requests_deduplicate_and_do_not_accept_changed_payload(identities):
    with acting(identities[0]):
        run = source(identities[0])
        key = uuid4().hex
        first = design.enqueue_design(run, idempotency_key=key)
        assert design.enqueue_design(run, idempotency_key=key)['id'] == first['id']
        with pytest.raises(HTTPException) as error:
            design.enqueue_design(run, platform='moments', idempotency_key=key)
        assert error.value.status_code == 409
        assert 'parameters' not in jobs.job_result(first['id'])


def test_design_cannot_use_another_tenants_source(identities):
    with acting(identities[0]):
        run = source(identities[0])
    with acting(identities[1]):
        with pytest.raises(HTTPException) as error:
            design.enqueue_design(run)
        assert error.value.status_code == 404


def test_design_export_rechecks_source_status(identities):
    owner = identities[0]
    with acting(owner):
        run = source(owner)
        identifier = uuid4().hex
        save_body('design', identifier, {'id': identifier, 'source_run_id': run, 'artboards': []})
        assert design.load_design(identifier)['source_run_id'] == run
        body = load_body('run', run)
        body['status'] = 'needs_revision'
        save_body('run', run, body)
        with pytest.raises(HTTPException) as error:
            design.load_design(identifier)
        assert error.value.status_code == 409


def test_design_queue_commits_artifact_and_recovers_without_reexecuting(identities, monkeypatch):
    owner = identities[0]
    with acting(owner):
        run = source(owner)
        queued = design.enqueue_design(run)
    calls = []
    async def execute(parameters):
        calls.append(parameters['design_id'])
        artifact = {'id': parameters['design_id'], 'title': '真实设计任务', 'source_run_id': run,
                    'status': 'ready', 'version': 1, 'platform': 'xiaohongshu', 'artboards': []}
        response = design.project_design(artifact)
        jobs.commit_artifact_result('design', artifact['id'], artifact, response)
        # Model a worker interrupted after its transaction committed.
        raise asyncio.CancelledError()
    monkeypatch.setattr(design, 'execute_design', execute)
    claimed = jobs._claim()
    asyncio.run(jobs.execute_claimed(claimed))
    assert state(queued['id'])['status'] == 'queued'
    asyncio.run(jobs.execute_claimed(jobs._claim()))
    assert state(queued['id'])['status'] == 'succeeded'
    assert len(calls) == 1
    with acting(owner):
        result = jobs.job_result(queued['id'])['result']
        assert design.load_design(result['id'])['title'] == '真实设计任务'


def test_revision_creates_a_new_version_without_overwriting_parent(identities):
    owner = identities[0]
    with acting(owner):
        run = source(owner)
        identifier = uuid4().hex
        parent = {'id': identifier, 'source_run_id': run, 'version': 1, 'platform': 'moments',
                  'status': 'ready', 'title': '旧图', 'artboards': []}
        save_body('design', identifier, parent)
        queued = design.enqueue_design(run, parent_id=identifier, instruction='字少一点')
        job = load_body('generation_job', queued['id'])
        assert job['parameters']['design_id'] != identifier
        assert job['parameters']['parent_id'] == identifier
        assert load_body('design', identifier) == parent


def test_agent_design_effect_survives_response_loss(identities, monkeypatch):
    from app.gtm_agent import jobs as agent_jobs
    from app.gtm_agent.repository import create_conversation, link_artifact, get_turn
    from app.gtm_agent.models import TurnInput
    from test_gtm_agent_core import scripted
    owner = identities[0]
    with acting(owner):
        run = source(owner)
        conversation = create_conversation()
        link_artifact(conversation['id'], 'run', run)
        turn = agent_jobs.enqueue_turn(conversation['id'], TurnInput(text='做一组小红书配图', client_turn_id=uuid4().hex))
    calls = []
    async def execute(parameters):
        calls.append(parameters)
        artifact = {'id': parameters['design_id'], 'title': '门店设计', 'source_run_id': run,
            'status': 'ready', 'version': 1, 'platform': 'xiaohongshu', 'artboards': []}
        jobs.commit_artifact_result('design', artifact['id'], artifact, design.project_design(artifact))
        raise RuntimeError('response lost after durable artifact commit')
    monkeypatch.setattr(design, 'execute_design', execute)
    planner = scripted({'action': 'tool', 'tool': 'read_artifact'},
                       {'action': 'tool', 'tool': 'create_design_bundle', 'arguments': {'platform': 'xiaohongshu'}})
    asyncio.run(agent_jobs.execute_claimed(agent_jobs.claim_next(), planner=planner))
    with acting(owner):
        completed = get_turn(conversation['id'], turn['id'])
        assert completed['status'] == 'completed'
        assert completed['artifact']['kind'] == 'design'
        assert design.load_design(completed['artifact']['id'])['source_run_id'] == run
    assert len(calls) == 1


def test_design_reference_memory_uses_scoped_summaries_and_revokes_with_source(identities):
    from app.design_reference import design_references
    owner = identities[0]
    with acting(owner):
        run = source(owner)
        identifier = uuid4().hex
        artifact = {'id': identifier, 'source_run_id': run, 'title': '设计', 'version': 1,
            'platform': 'xiaohongshu', 'status': 'ready', 'artboards': [],
            'plan': {'selected_concept_id': 'first', 'concepts': [{'id': 'first', 'visual_thesis': '细节',
                      'composition': [], 'background': '#FFFFFF', 'foreground': '#000000'}]}}
        jobs.commit_artifact_result('design', identifier, artifact, design.project_design(artifact))
        assert design_references('xiaohongshu')['recent_compositions'][0]['design_id'] == identifier
        source_body = load_body('run', run)
        save_body('run', run, {**source_body, 'status': 'needs_revision'})
        assert not design_references('xiaohongshu')['recent_compositions']
    with acting(identities[1]):
        assert not design_references('xiaohongshu')['recent_compositions']


def test_design_can_use_other_verified_angles_but_never_another_product(monkeypatch):
    import io
    from PIL import Image
    from fastapi.responses import Response
    stream = io.BytesIO()
    Image.new('RGB', (64, 64), '#445566').save(stream, 'PNG')
    calls = []
    async def selected(run, identifier):
        calls.append(identifier)
        return Response(stream.getvalue(), media_type='image/png')
    async def catalog(identifier):
        calls.append(identifier)
        return Response(stream.getvalue(), media_type='image/png')
    monkeypatch.setattr('app.api.run_asset', selected)
    monkeypatch.setattr('app.api.asset', catalog)
    record = {'images': [{'asset_id': 'side'}], '_poster_task': {'product': 'HUAWEI Mate 90'},
        'assets': [{'id': 'side', 'product': 'HUAWEI Mate 90'},
                   {'id': 'front', 'product': 'HUAWEI Mate 90', 'verified': True, 'provenance': 'huawei_official'},
                   {'id': 'wrong-phone', 'product': 'Other Phone', 'verified': True, 'provenance': 'huawei_official'},
                   {'id': 'unverified', 'product': 'HUAWEI Mate 90', 'verified': False, 'provenance': 'huawei_official'}]}
    assets = asyncio.run(design.collect_assets('authorized-run', record))
    assert set(assets) == {'side', 'front'}
    assert calls == ['side', 'front']
