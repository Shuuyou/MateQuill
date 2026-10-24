"""Legacy display placement gaps normalize without creating evidence or assets."""
import asyncio
from copy import deepcopy

import pytest
from pydantic import TypeAdapter, ValidationError

from app.llm import OpenAICompatibleLLM
from app.models import ContentBlock, ImagePlacement, TaskInput
from app.workflow import AgentWorkflow, normalize_generated_images
from test_moments_workflow import Model, Provider, PRODUCT, content, evidence
from test_gtm_agent_core import identities


def parse(images, plan=None, count=1):
    return TypeAdapter(list[ImagePlacement]).validate_python(normalize_generated_images(images, plan or {}, count))


@pytest.mark.parametrize('row,plan,position', [
    ({'asset_id': 'a1', 'role': 'cover', 'caption': None, 'reason': None}, {}, '封面'),
    ({'asset_id': 'a1', 'block_index': 0}, {}, '第1段后'),
    ({'asset_id': 'a1'}, {'cards': [{'role': 'cover', 'asset_ids': ['a1']}]}, '封面'),
])
def test_only_explicit_cover_or_existing_block_binding_supplies_position(row, plan, position):
    before = deepcopy(row)
    image = parse([row], plan)[0]
    assert image.position == position
    assert image.asset_id == 'a1' and image.caption == '' and image.reason == ''
    assert image.supported_fact_ids == []
    assert row == before


@pytest.mark.parametrize('row', [
    {'asset_id': 'a1'},
    {'asset_id': 'a1', 'block_index': 3},
    {'asset_id': 'a1', 'block_index': True},
    {'role': 'cover'},
    {'asset_id': 'a1', 'role': 'cover', 'supported_fact_ids': None},
])
def test_missing_identity_unbound_position_or_null_citations_remain_errors(row):
    with pytest.raises(ValidationError): parse([row])


def test_existing_caption_reason_and_unknown_asset_are_never_replaced():
    row = {'asset_id': 'unknown', 'role': 'cover', 'caption': '原有说明', 'reason': '原有意图',
           'supported_fact_ids': ['unknown-fact']}
    image = parse([row])[0]
    assert image.caption == row['caption'] and image.reason == row['reason']
    assert image.asset_id == 'unknown' and image.supported_fact_ids == ['unknown-fact']


def test_full_workflow_accepts_missing_display_fields_then_runs_existing_reviews():
    draft = content()
    draft['images'][0].pop('position')
    draft['images'][0]['caption'] = None
    class ReviewedModel(Model):
        def __init__(self): super().__init__(draft); self.verified = 0
        async def verify(self, result, bundle):
            self.verified += 1
            return await super().verify(result, bundle)
    model = ReviewedModel()
    task = TaskInput(product=PRODUCT, audience='关注本店的朋友', scene='展示今天的桌面', platform='moments', mode='live')
    result = asyncio.run(AgentWorkflow(Provider(), model).run(task))
    assert result.status == 'ready' and model.verified == 1
    assert result.images[0].position == '封面' and result.images[0].caption == ''


def test_unrepairable_structure_records_only_sanitized_loc_and_type_without_regeneration():
    draft = content()
    draft['images'][0].pop('asset_id')
    draft['images'][0]['private_test_marker'] = 'PRIVATE_WRITER_CANARY'
    model = Model(draft)
    task = TaskInput(product=PRODUCT, audience='关注本店的朋友', scene='展示今天的桌面', platform='moments', mode='live')
    result = asyncio.run(AgentWorkflow(Provider(), model).run(task))
    assert result.status == 'blocked'
    assert result.strategy['generation_error']['code'] == 'invalid_generated_structure'
    assert result.strategy['generation_schema_errors'] == [{'field': 'images.0.asset_id', 'type': 'missing'}]
    assert model.last_generated_data == draft
    assert 'PRIVATE_WRITER_CANARY' not in str(result.model_dump())


def test_writer_receives_actual_nested_schemas_and_retains_returned_json():
    model = OpenAICompatibleLLM(api_key='synthetic-unused')
    model.marketing_context = {'publisher': {}}
    model.prepared_creative_direction = {'status': 'prepared_direction_reused', 'selected_concept': None}
    sent = []
    async def writer(system, payload, temperature):
        sent.append(payload)
        return content()
    model._json = writer
    task = TaskInput(product=PRODUCT, audience='关注本店的朋友', scene='展示今天的桌面', platform='moments', mode='live')
    result = asyncio.run(model.generate(task, evidence(), {}))
    contract = sent[-1]['output_contract']
    assert contract['blocks']['items']['required'] == ContentBlock.model_json_schema()['required']
    assert contract['images']['items']['required'] == ImagePlacement.model_json_schema()['required']
    assert contract['blocks']['items']['properties']['fact_ids']['items']['enum'] == ['f1']
    assert contract['images']['items']['properties']['supported_fact_ids']['items']['enum'] == ['f1']
    assert contract['images']['items']['properties']['asset_id']['enum'] == ['a1']
    assert {'asset_id', 'caption', 'position'} <= set(contract['images']['items']['required'])
    assert model.last_generated_data == result

@pytest.mark.real_tenancy
def test_active_turn_keeps_raw_failed_writer_output_only_as_private_diagnostics(identities):
    from app.gtm_agent import jobs
    from app.gtm_agent.repository import TURN
    from app.gtm_agent.tools import AgentTools
    from app.tenancy import authorize_resource
    from test_gtm_agent_core import acting, start, scripted, Provider as ToolProvider
    owner = identities[0]
    _, turn = start(owner)
    draft = content()
    draft['images'][0].pop('asset_id')
    draft['images'][0]['private_test_marker'] = 'RAW_PRIVATE_CANARY'
    async def generator(task):
        live_task = task.model_copy(update={'product': PRODUCT, 'mode': 'live'})
        return (await AgentWorkflow(Provider(), Model(draft)).run(live_task)).model_dump()
    planner = scripted({'action': 'tool', 'tool': 'read_catalog'},
        {'action': 'tool', 'tool': 'read_product', 'arguments': {'product': 'HUAWEI MateBook Pro'}},
        {'action': 'tool', 'tool': 'generate_content', 'arguments': {
            'product': 'HUAWEI MateBook Pro', 'audience': '附近居民', 'scene': '整理笔记'}})
    asyncio.run(jobs.execute_claimed(jobs.claim_next(), planner=planner,
        tools_factory=lambda c, t: AgentTools(c, t, provider=ToolProvider(), generator=generator)))
    with acting(owner):
        saved = authorize_resource(TURN, turn['id'])['body']
    diagnostics = saved['generation_diagnostics']
    assert diagnostics['approved'] is False
    assert diagnostics['raw_writer_output'] == draft
    assert diagnostics['schema_errors'] == [{'field': 'images.0.asset_id', 'type': 'missing'}]
    assert 'RAW_PRIVATE_CANARY' not in str(saved['observations'])
    assert saved.get('artifact') is None
