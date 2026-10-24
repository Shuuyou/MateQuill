"""Scoped Agent micro-adjustments keep the server-owned design baseline."""
import asyncio
from contextlib import nullcontext
from copy import deepcopy
from uuid import uuid4

import pytest
from fastapi import HTTPException
from pydantic import ValidationError

from app import design_service
from app.gtm_agent import tools as module
from app.models import TaskInput
from app.tenancy import save_body, load_body
from test_gtm_agent_core import identities, acting

pytestmark = pytest.mark.real_tenancy


def edit(**changes):
    return {'page_id': 'page-1', 'region_id': 'headline-1',
            'values': {'letter_spacing': -1.5, 'font_scale': 0.05},
            'goal': '收紧标题字距，保留原句和素材', **changes}


def plan():
    return {'selected_concept_id': 'selected', 'brief': {
        'texts': {'headline': {'text': '准确原句', 'fact_ids': []}}, 'asset_ids': ['real-product']},
        'concepts': [{'id': 'selected', 'pages': [{'id': 'page-1', 'role': 'cover',
            'composition': [{'id': 'headline-1', 'role': 'headline', 'text_id': 'headline',
                'x': 0.1, 'y': 0.1, 'width': 0.8, 'height': 0.2, 'font_scale': 0.06,
                'letter_spacing': 0}]}]}], 'provenance': {'base_plan': {'secret': 'not projected'}}}


def seed(owner, *, audience='private'):
    run_id, design_id, turn_id = [uuid4().hex for _ in range(3)]
    save_body('run', run_id, {'run_id': run_id, 'status': 'ready', 'title': '原稿',
        '_poster_task': TaskInput(product='HUAWEI 合成机型', mode='demo',
                                 store_id=owner['store']['id']).model_dump()})
    save_body('design', design_id, {'id': design_id, 'source_run_id': run_id,
        'title': '原图', 'status': 'ready', 'version': 1, 'platform': 'moments',
        'artboards': [], 'plan': plan()})
    save_body(module.TURN, turn_id, {'effects': {}})
    tool = object.__new__(module.AgentTools)
    tool.turn = {'id': turn_id, 'text': '把标题字距再收紧一些'}
    tool.conversation = {'current_artifact': {'kind': 'design', 'id': design_id}}
    tool.audience, tool.opportunity = audience, None
    return tool, run_id, design_id


def test_arguments_parse_typed_controls_but_reject_client_baseline_or_copy_replacement():
    parsed = module.Arguments.model_validate({'craft_edit': edit()})
    assert parsed.craft_edit.model_dump() == edit()
    for payload in [edit(base_plan=plan()), edit(values={'text_id': 'new-copy'}),
                    edit(values={'asset_id': 'another-phone'}), edit(source_run_id='another-source')]:
        with pytest.raises(ValidationError):
            module.Arguments.model_validate({'craft_edit': payload})
    with pytest.raises(ValidationError):
        module.Arguments.model_validate({'craft_edit': edit(), 'parent_id': 'another-design'})


def test_execute_passes_micro_controls_bound_to_current_authorized_design(identities, monkeypatch):
    calls = []
    monkeypatch.setattr(module, 'bind_effect', lambda *a, **kw: nullcontext())
    async def execute(parameters):
        calls.append(deepcopy(parameters))
        save_body('design', parameters['design_id'], {'id': parameters['design_id'],
            'source_run_id': parameters['source_run_id'], 'title': '调整后', 'status': 'ready',
            'platform': 'moments', 'version': 2, 'artboards': []})
        return {'id': parameters['design_id']}
    monkeypatch.setattr(design_service, 'execute_design', execute)
    with acting(identities[0]):
        tool, run_id, parent_id = seed(identities[0])
        read = asyncio.run(tool.execute('read_artifact', {}))
        assert read['plan']['pages'][0]['composition'][0]['id'] == 'headline-1'
        assert read['plan']['brief']['texts']['headline']['text'] == '准确原句'
        assert 'provenance' not in read['plan'] and 'secret' not in str(read)
        result = asyncio.run(tool.execute('revise_design', {'craft_edit': edit(), 'instruction': '小幅收紧'}))
        assert result['artifact']['kind'] == 'design'
        assert calls[0]['parent_id'] == parent_id and calls[0]['source_run_id'] == run_id
        assert calls[0]['platform'] == 'moments' and calls[0]['craft_edit'] == edit()
        assert calls[0]['instruction'] == '小幅收紧'
        assert load_body('design', parent_id)['version'] == 1
        tool.conversation['current_artifact'] = {'kind': 'design', 'id': parent_id}
        asyncio.run(tool.execute('revise_design', {'craft_edit': edit(values={'letter_spacing': -2.0})}))
        assert calls[0]['design_id'] != calls[1]['design_id']


def test_foreign_design_and_group_private_design_never_reach_executor(identities, monkeypatch):
    calls = []
    async def execute(parameters):
        calls.append(parameters)
        return {}
    monkeypatch.setattr(design_service, 'execute_design', execute)
    with acting(identities[0]):
        tool, _, parent_id = seed(identities[0])
        tool.audience = 'group'
        with pytest.raises(HTTPException) as error:
            asyncio.run(tool.execute('revise_design', {'craft_edit': edit()}))
        assert error.value.status_code == 404
        tool.audience = 'private'
    with acting(identities[1]):
        with pytest.raises(HTTPException) as error:
            asyncio.run(tool.execute('revise_design', {'craft_edit': edit()}))
        assert error.value.status_code == 404
    assert calls == []


def test_controls_cannot_be_sent_to_creation_or_a_plain_run(identities):
    with acting(identities[0]):
        tool, run_id, _ = seed(identities[0])
        with pytest.raises(HTTPException) as error:
            asyncio.run(tool.execute('create_design_bundle', {'craft_edit': edit()}))
        assert error.value.status_code == 422
        tool.conversation['current_artifact'] = {'kind': 'run', 'id': run_id}
        with pytest.raises(HTTPException) as error:
            asyncio.run(tool.execute('revise_design', {'craft_edit': edit()}))
        assert error.value.status_code == 422


def test_micro_edit_never_reselects_source_after_marketing_rejection(identities, monkeypatch):
    calls = []
    monkeypatch.setattr(module, 'bind_effect', lambda *a, **kw: nullcontext())
    async def execute(parameters):
        calls.append(parameters)
        raise HTTPException(422, {'code': 'marketing_reselect', 'feedback': ['需重新策划']})
    monkeypatch.setattr(design_service, 'execute_design', execute)
    with acting(identities[0]):
        tool, _, _ = seed(identities[0])
        with pytest.raises(HTTPException) as error:
            asyncio.run(tool.execute('revise_design', {'craft_edit': edit()}))
        assert error.value.detail['code'] == 'marketing_reselect'
    assert len(calls) == 1


def test_plan_projection_preserves_whole_regions_and_supports_indexed_focus():
    body = {'plan': plan()}
    regions = body['plan']['concepts'][0]['pages'][0]['composition']
    regions[0]['path_d'] = 'M0 0 ' * 80
    regions.append({'id': 'last-region', 'role': 'shape', 'x': 0.8, 'y': 0.8,
                    'width': 0.1, 'height': 0.1})
    result = module.project_design_plan(body, limit=700)
    assert result['has_more'] is True
    assert result['page_index'][0]['region_ids'] == ['headline-1', 'last-region']
    focused = module.project_design_plan(body, focus='last-region', limit=700)
    assert focused['pages'][0]['composition'][0]['id'] == 'last-region'
    assert all(row in regions for row in focused['pages'][0]['composition'])
