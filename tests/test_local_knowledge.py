"""New judging scope: formal local evidence must not depend on ima availability."""
import asyncio
import hashlib
import json
from pathlib import Path

from app.local_knowledge import LocalKnowledgeProvider
from app.models import TaskInput
from app.workflow import AgentWorkflow
from tests.test_workflow_adversarial import evidence, draft, Provider, Model, PRODUCT


def local_evidence():
    bundle=evidence()
    bundle.verified_live=False
    bundle.verified_local=True
    for source in bundle.sources:
        source.provider='local_archive'
        source.sha256=hashlib.sha256(source.excerpt.encode()).hexdigest()
    for asset in bundle.assets:
        asset.provenance='local_archive'
        asset.sha256='a'*64
    return bundle


def execute(bundle):
    task=TaskInput(product=PRODUCT,audience='学生',scene='课堂学习',platform='moments',mode='local')
    return asyncio.run(AgentWorkflow(Provider(bundle),Model(draft())).run(task))


def test_local_formal_generation_can_pass_without_any_ima_evidence():
    result=execute(local_evidence())
    assert result.status=='ready' and result.mode=='local'
    assert all(s.provider=='local_archive' for s in result.sources)


def test_local_formal_does_not_promote_demo_sources():
    bundle=local_evidence()
    bundle.sources[0].provider='local_demo'
    assert execute(bundle).status=='blocked'


def test_local_formal_rejects_unverified_or_altered_spans():
    bundle=local_evidence()
    bundle.sources[0].sha256='b'*64
    assert execute(bundle).status=='blocked'
    bundle=local_evidence()
    bundle.verified_local=False
    assert execute(bundle).status=='blocked'


def test_local_formal_needs_a_real_model():
    task=TaskInput(product=PRODUCT,audience='学生',scene='课堂学习',platform='moments',mode='local')
    result=asyncio.run(AgentWorkflow(Provider(local_evidence())).run(task))
    assert result.status=='blocked'


def test_archive_provider_preserves_original_and_rechecks_asset_bytes(tmp_path):
    folder=tmp_path/'P1-产品参数库'/'sample'
    (folder/'images').mkdir(parents=True)
    (folder/'产品事实.md').write_text('# TestBook\n\n## 内存\n\n容量\n16GB\n',encoding='utf-8')
    image=folder/'images'/'sample.png'
    image.write_bytes(b'\x89PNG\r\n\x1a\nsynthetic')
    (folder/'图片索引.csv').write_text('产品型号,文件名,官网图片说明\nTestBook,sample.png,产品外观\n',encoding='utf-8')
    provider=LocalKnowledgeProvider(tmp_path,tmp_path/'none.json',tmp_path/'none2.json')
    bundle=asyncio.run(provider.retrieve('TestBook'))
    assert bundle.verified_local and not bundle.verified_live
    assert bundle.sources[0].provider=='local_archive'
    assert bundle.assets[0].provenance=='local_archive'
    aid=bundle.assets[0].id
    assert asyncio.run(provider.get_asset(aid))==image
    image.write_bytes(b'changed bytes')
    assert asyncio.run(provider.get_asset(aid)) is None


def test_collected_official_specs_have_verbatim_spans_and_scoped_urls():
    root=Path(__file__).resolve().parents[1]
    document=json.loads((root/'data/official_products.json').read_text(encoding='utf-8'))
    assert len(document['products'])>=10
    for product in document['products']:
        assert product['spec_url'].startswith('https://consumer.huawei.com/')
        assert product['facts']
        for fact in product['facts']:
            assert fact['value'] in fact['quote']
            assert fact['source_url']==product['spec_url']
