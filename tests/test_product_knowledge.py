import asyncio
import hashlib
import io

import pytest
from PIL import Image

from app.models import TaskInput, EvidenceBundle, Fact, Source
from app.product_knowledge import (ProductPreviewInput, ProductRepository, source_preview,
                                    resolve_ecosystem, load_products, capability_fact)
from app.local_knowledge import LocalKnowledgeProvider
from app.task_context import TaskContextProvider
from app.media_intake import MediaStore
from app.run_records import save_run, load_run
from app.workflow import AgentWorkflow


def test_import_requires_review_and_persists_confirmed_evidence(tmp_path):
    repo = ProductRepository(tmp_path/'products.sqlite3')
    payload = ProductPreviewInput(name='HUAWEI 研究样机', category='phone',
        source_url='https://example.com/product-sheet',
        source_text='# HUAWEI 研究样机\n\n## 屏幕\n尺寸：6.8 英寸\n\n## 操作系统\nHarmonyOS 6.1')
    draft = asyncio.run(source_preview(payload, repo))
    assert repo.list_products() == []
    assert draft['facts'] and draft['provider'] == 'store_knowledge'
    assert all(f['value'] in f['quote'] for f in draft['facts'])
    saved = repo.confirm(draft['draft_id'])
    assert saved['reviewed'] and saved['reviewed_at']
    assert ProductRepository(repo.path).list_products()[0]['name'] == payload.name
    with pytest.raises(KeyError): repo.confirm(draft['draft_id'])


def test_nonofficial_source_needs_explicit_text_not_arbitrary_network(tmp_path):
    with pytest.raises(ValueError, match='原文'):
        asyncio.run(source_preview(ProductPreviewInput(name='测试产品',
            source_url='https://example.com/product'), ProductRepository(tmp_path/'products.sqlite3')))


@pytest.mark.parametrize('url',['http://127.0.0.1/a','https://localhost/a','https://user:password@example.com/a','https://example.com:8000/a'])
def test_ingestion_rejects_local_and_credential_urls(tmp_path,url):
    with pytest.raises(ValueError):
        asyncio.run(source_preview(ProductPreviewInput(name='测试产品',source_url=url,
            source_text='## 屏幕\n尺寸：6英寸'),ProductRepository(tmp_path/'p.sqlite3')))


def test_catalog_has_verified_phone_and_tablet_categories():
    rows=asyncio.run(LocalKnowledgeProvider().catalog())
    by_name={row['name']:row for row in rows}
    assert by_name['HUAWEI Mate 80']['category']=='phone'
    assert by_name['HUAWEI MatePad Edge']['category']=='tablet'
    assert len(rows)>=13


def test_ecosystem_plan_has_scoped_conditions_and_real_task():
    result=resolve_ecosystem(['HUAWEI MateBook Pro','HUAWEI Mate 80','HUAWEI MatePad Edge'],
                            '整理手机照片并写策划文档','内容运营','moments')
    assert result['status']=='ready'
    assert len(result['task']['ecosystem_products'])==2
    assert result['capabilities'][0]['conditions']
    assert '同一华为账号' in ' '.join(result['capabilities'][0]['conditions'])
    TaskInput.model_validate(result['task'])


def test_ecosystem_does_not_infer_collaboration_for_unknown_model():
    result=resolve_ecosystem(['HUAWEI Mate 80','HUAWEI MatePad Edge'],'写作','学生','moments')
    assert result['capabilities']==[]
    with pytest.raises(ValueError):resolve_ecosystem(['HUAWEI Mate 80','ImaginaryBook'])


def test_media_context_never_turns_ocr_into_product_facts(tmp_path):
    store=MediaStore(tmp_path/'media');output=io.BytesIO();Image.new('RGB',(40,40),'green').save(output,'JPEG')
    identifier=store.put(output.getvalue(),'image/jpeg','.jpg')
    store.save_result(identifier,{'observations':{'caption':'门店陈列照片','visible_text':['999GB内存']}})
    class Provider:
        async def retrieve(self,*args):return EvidenceBundle(product='测试产品',verified_local=True)
    task=TaskInput(product='测试产品',media_ids=[identifier],mode='local')
    bundle=asyncio.run(TaskContextProvider(Provider(),task,store).retrieve(task.product))
    assert bundle.facts==[]
    assert bundle.assets[0].provenance=='store_upload'
    assert bundle.assets[0].sha256==hashlib.sha256(output.getvalue()).hexdigest()
    assert bundle.user_materials[0]['observations']['visible_text']==['999GB内存']


def test_selected_secondary_device_is_allowed_but_third_model_is_rejected():
    quote='屏幕尺寸：6.75英寸';sid='source-1'
    bundle=EvidenceBundle(product='电脑',verified_local=True,
        facts=[Fact(id='phone-screen',product='手机',label='屏幕',value='6.75英寸',quote=quote,source_id=sid)],
        sources=[Source(id=sid,title='原文',provider='huawei_official',excerpt=quote,sha256=hashlib.sha256(quote.encode()).hexdigest())])
    from app.models import RunResult, ContentBlock
    task=TaskInput(product='电脑',ecosystem_products=['手机'],mode='local',platform='moments')
    result=RunResult(mode='local',blocks=[ContentBlock(text='手机的屏幕尺寸为6.75英寸。',fact_ids=['phone-screen'])])
    issues=AgentWorkflow(None)._audit(task,result,bundle)
    assert not [a for a in issues if a.category=='model']
    task.ecosystem_products=[]
    assert any(a.category=='model' for a in AgentWorkflow(None)._audit(task,result,bundle))


def test_run_records_survive_new_connections_and_invalid_ids_are_not_paths(tmp_path):
    path=tmp_path/'runs.sqlite3';record={'run_id':'r1','status':'ready','title':'实际结果'}
    save_run('r1',record,path)
    assert load_run('r1',path)==record
    assert load_run('../../secret',path) is None


def test_official_os_overrides_archive_folder_label(monkeypatch):
    from app.demo import DemoProvider
    from app.local_knowledge import LocalKnowledgeProvider
    from app.product_knowledge import summary
    name='HUAWEI MateBook GT 14'
    row={'name':name,'facts':[{'label':'操作系统','value':'Windows 11 家庭版\nLinux V20 版'}]}
    async def archive_catalog(self):
        return [{'id':'legacy','name':name,'system':'鸿蒙'}]
    monkeypatch.setattr(DemoProvider,'catalog',archive_catalog)
    provider=object.__new__(LocalKnowledgeProvider)
    monkeypatch.setattr(provider,'_product_records',lambda:[row])
    result=asyncio.run(provider.catalog())
    assert result[0]['system']==row['facts'][0]['value']
    assert summary(row)['system']==row['facts'][0]['value']


def test_semifinal_visual_policy_accepts_one_relevant_image_without_padding():
    from tests.test_workflow_adversarial import evidence,draft,PRODUCT
    from app.models import RunResult
    bundle=evidence();result=RunResult.model_validate(draft())
    task=TaskInput(product=PRODUCT,audience='学生',scene='资料整理',platform='xiaohongshu',mode='live')
    issues=AgentWorkflow(None)._audit(task,result,bundle)
    assert not any(a.category=='image_count' for a in issues)
    result.images=[]
    assert any(a.category=='image_count' and a.severity=='error' for a in AgentWorkflow(None)._audit(task,result,bundle))


def test_run_retention_keeps_the_newest_writes_when_timestamps_tie(tmp_path):
    from app.run_records import connect
    path = tmp_path / 'retention.sqlite3'
    with connect(path) as connection:
        connection.executemany('INSERT INTO runs(id,body,created_at) VALUES(?,?,?)',
                               [(f'run-{index}', '{}', 9999999999) for index in range(200)])
    # Explicitly tie the timestamp to exercise stable ordering independently
    # of the machine clock; the latest replacement acquires a newer rowid.
    with connect(path) as connection:
        connection.execute('INSERT INTO runs(id,body,created_at) VALUES(?,?,?)', ('newest', '{"title":"new"}', 9999999999))
    save_run('trigger', {'title': 'trigger'}, path)
    assert load_run('newest', path) == {'title': 'new'}
    assert load_run('run-0', path) is None


def test_ecosystem_model_identifier_is_not_mistaken_for_a_parameter_claim():
    from app.models import RunResult, ContentBlock
    quote = '机身颜色：雪域白'
    source = Source(id='phone-source', title='手机原文', provider='huawei_official', excerpt=quote,
                    sha256=hashlib.sha256(quote.encode()).hexdigest())
    fact = Fact(id='phone-color', product='HUAWEI Mate 80', label='机身颜色', value='雪域白', quote=quote,
                source_id=source.id)
    bundle = EvidenceBundle(product='HUAWEI MateBook Pro', verified_local=True, facts=[fact], sources=[source])
    task = TaskInput(product=bundle.product, ecosystem_products=[fact.product], platform='moments', mode='local')
    result = RunResult(mode='local', blocks=[ContentBlock(text='Mate 80的雪域白配色，适合放进这次设备选色清单。', fact_ids=[fact.id])])
    assert not [issue for issue in AgentWorkflow(None)._audit(task, result, bundle) if issue.category == 'number']
    result.blocks[0].text = 'Mate 80配备80GB内存。'
    assert any(issue.category == 'number' for issue in AgentWorkflow(None)._audit(task, result, bundle))
    result.blocks[0].text = 'Mate 800的雪域白配色。'
    assert any(issue.category == 'number' for issue in AgentWorkflow(None)._audit(task, result, bundle))


def test_user_photo_cannot_prove_specs_even_when_the_spec_has_a_valid_text_fact():
    from app.editorial import image_editorial_audit
    from app.models import RunResult, ContentBlock, Asset, ImagePlacement
    fact = Fact(id='f1', product='测试产品', label='内存', value='16GB', quote='内存16GB', source_id='s1')
    asset = Asset(id='photo1', product='测试产品', url='/api/media/photo1', source_id='upload1',
                  provenance='store_upload', visual_role='scene', title='门店桌面照片', verified=True)
    bundle = EvidenceBundle(product='测试产品', facts=[fact], assets=[asset])
    result = RunResult(blocks=[ContentBlock(text='内存16GB。', fact_ids=['f1'])], images=[
        ImagePlacement(asset_id='photo1', caption='门店桌面', position='正文第1段', block_index=0,
                       role='context', reason='展示本店环境', supported_fact_ids=['f1'])])
    assert any(issue.severity == 'error' and '上传照片' in issue.message for issue in image_editorial_audit(result, bundle))
    result.images[0].supported_fact_ids = []
    assert not [issue for issue in image_editorial_audit(result, bundle) if issue.severity == 'error']
