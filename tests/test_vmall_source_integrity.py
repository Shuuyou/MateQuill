"""VMALL page-byte and extracted-text hashes have distinct, enforced meanings."""
import asyncio
from copy import deepcopy
import hashlib

import pytest

from app.creative_revision import _snapshot_bundle
from app.models import ContentBlock, RunResult, TaskInput
from app.vmall_catalog import valid_vmall_source
from app.vmall_knowledge import parameter_evidence
from app.workflow import AgentWorkflow
from test_vmall_knowledge import row


def task(bundle):
    return TaskInput(product=bundle.product, audience='关注本店的人', scene='了解产品细节',
                     platform='moments', mode='local')


def test_page_hash_is_preserved_and_excerpt_hash_uses_actual_derived_bytes():
    snapshot = row()
    bundle = parameter_evidence(snapshot)
    source = bundle.sources[0]
    assert source.sha256 == snapshot['source_sha256']
    assert source.excerpt_sha256 == hashlib.sha256(source.excerpt.encode('utf-8')).hexdigest()
    assert source.sha256 != source.excerpt_sha256
    assert valid_vmall_source(source)
    assert AgentWorkflow(None)._audit_bundle_evidence(task(bundle), bundle) == []
    roundtrip = type(bundle).model_validate(bundle.model_dump())
    assert roundtrip.sources[0].sha256 == source.sha256
    assert roundtrip.sources[0].excerpt_sha256 == source.excerpt_sha256


@pytest.mark.parametrize('change', ['excerpt', 'missing_excerpt_hash', 'page_hash', 'url', 'quote', 'value', 'product'])
def test_invalid_evidence_stops_before_generation_or_repair(change):
    bundle = parameter_evidence(row())
    if change == 'excerpt': bundle.sources[0].excerpt += '\n额外文字'
    if change == 'missing_excerpt_hash': bundle.sources[0].excerpt_sha256 = ''
    if change == 'page_hash': bundle.sources[0].sha256 = ''
    if change == 'url': bundle.sources[0].url = 'https://unrelated.example/product/1001.html'
    if change == 'quote': bundle.facts[0].quote = '不在来源中的内容'
    if change == 'value': bundle.facts[0].value = '引用中没有的值'
    if change == 'product': bundle.facts[0].product = '另一型号'
    class Provider:
        async def retrieve(self, *_): return deepcopy(bundle)
    class ForbiddenModel:
        enabled = True
        async def generate(self, *_): pytest.fail('Invalid evidence must never reach paid writing')
        async def repair(self, *_): pytest.fail('A text rewrite cannot repair source integrity')
    result = asyncio.run(AgentWorkflow(Provider(), ForbiddenModel()).run(task(bundle)))
    assert result.status == 'blocked'
    assert any(item.severity == 'error' for item in result.audit)
    assert any(item.stage == 'evidence_preflight' for item in result.trace)
    assert result.strategy['generation_timing']['calls'] == []


def test_revision_derived_excerpt_retains_original_page_association():
    bundle = parameter_evidence(row())
    selected = bundle.facts[0]
    result = RunResult(facts=bundle.facts, sources=bundle.sources,
                       blocks=[ContentBlock(text=selected.value, fact_ids=[selected.id])])
    derived = _snapshot_bundle(task(bundle), result)
    assert len(derived.facts) == 1
    source = derived.sources[0]
    assert source.sha256 == bundle.sources[0].sha256
    assert source.excerpt == selected.quote
    assert source.excerpt_sha256 == hashlib.sha256(selected.quote.encode('utf-8')).hexdigest()
    assert source.excerpt_sha256 != bundle.sources[0].excerpt_sha256
    assert AgentWorkflow(None)._audit_bundle_evidence(task(bundle), derived) == []
    assert bundle.sources[0].excerpt != selected.quote
