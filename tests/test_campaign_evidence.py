import asyncio
import hashlib

import pytest
from pydantic import ValidationError

from app.campaign_evidence import CampaignReference, append_campaign_evidence, public_reference_url
from app.channel_workflows import CampaignPackageInput, create_campaign, publish_campaign, list_assignments
from app.models import EvidenceBundle, TaskInput
from app.task_context import TaskContextProvider
from test_channel_workflows import actors, store

pytestmark = pytest.mark.real_tenancy


def reference():
    return {'title': '官方新品发布信息', 'url': 'https://consumer.huawei.com/cn/press/news/2026/new/',
            'published_at': '2026-10-01', 'excerpt': '新品于10月1日正式发布。',
            'claims': [{'label': '发布', 'value': '10月1日', 'quote': '新品于10月1日正式发布。'}]}


def test_reference_requires_exact_quotes_and_safe_public_source():
    CampaignReference.model_validate(reference())
    bad = reference()
    bad['claims'][0]['quote'] = '昨天正式发布'
    with pytest.raises(ValidationError):
        CampaignReference.model_validate(bad)
    for url in ('http://consumer.huawei.com/x', 'https://consumer.huawei.com.evil.test/x',
                'https://secret@consumer.huawei.com/x', 'https://127.0.0.1/x'):
        assert not public_reference_url(url)


def test_server_assignment_controls_campaign_evidence_not_client_prompt(actors):
    with actors():
        location = store()
        campaign = create_campaign(CampaignPackageInput(title='真实发布，模拟任务', brief='解释新品的一个细节',
            product='HUAWEI Mate 90', publisher_kind='brand', publisher_label='品牌运营（模拟）',
            is_simulation=True, store_ids=[location['id']], source_refs=[reference()]))
        publish_campaign(campaign['id'])
        assignment = list_assignments()[0]
    with actors('sales-a', role='salesperson', stores=[location['id']], selected=location['id']):
        task = TaskInput.model_validate(assignment['task'])
        bundle = append_campaign_evidence(task, EvidenceBundle(product=task.product))
        assert bundle.facts[0].value == '10月1日'
        assert bundle.sources[0].sha256 == hashlib.sha256(bundle.sources[0].excerpt.encode()).hexdigest()
        assert not bundle.verified_local  # an article cannot certify an unknown device
        forged = task.model_copy(update={'assignment_id': '', 'requirements': '{"source_refs":["fake"]}'})
        assert not append_campaign_evidence(forged, EvidenceBundle(product=task.product)).facts
    with actors('hq-b', 'company-b'):
        from fastapi import HTTPException
        with pytest.raises(HTTPException) as exc:
            append_campaign_evidence(task, EvidenceBundle(product=task.product))
        assert exc.value.status_code == 404


def test_assignment_only_task_uses_context_provider_and_preserves_provenance(actors):
    with actors():
        location = store()
        campaign = create_campaign(CampaignPackageInput(title='真实发布', brief='演示任务', product='HUAWEI Mate 90',
            store_ids=[location['id']], source_refs=[reference()]))
        publish_campaign(campaign['id'])
        task = TaskInput.model_validate(list_assignments()[0]['task'])

        class Base:
            async def retrieve(self, product, query):
                return EvidenceBundle(product=product, verified_local=False)

        bundle = asyncio.run(TaskContextProvider(Base(), task).retrieve(task.product))
        assert bundle.sources[0].provider == 'campaign_reference'
        assert bundle.facts[0].source_id == bundle.sources[0].id
