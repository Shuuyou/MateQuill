"""SKU-bound parameter retrieval and source-byte delivery; no live network/model."""
import asyncio
from copy import deepcopy
import hashlib
from io import BytesIO
import json

import httpx
from PIL import Image
import pytest

from app import vmall_knowledge as vmall
from app.local_knowledge import LocalKnowledgeProvider
from app.llm import OpenAICompatibleLLM


def row(**changes):
    result={'catalog_key':'vmall-product-1001','spu_id':'1001','name':'合成手机 Pro',
        'source_url':'https://www.vmall.com/product/1001.html','source_sha256':hashlib.sha256(b'public-official-page').hexdigest(),
        'captured_at':'2026-10-24T20:00:00+08:00','category':'phone','parameter_sku_id':'11001',
        'brand':'合成品牌','seller':{'name':'示例商家'},
        'variants':[{'sku_id':'11001','name':'合成手机 Pro 8GB+128GB 黑色'},
                    {'sku_id':'11002','name':'合成手机 Pro 12GB+256GB 蓝色'}],
        'parameter_sections':[{'name':'主要参数','data':[
            {'attrName':'操作系统','attrValue':'HarmonyOS 测试版'},
            {'attrName':'屏幕尺寸','attrValue':'6.75 英寸','attrRemark':'*按标准矩形测量。<br>实际可视区域略小。'},
            {'attrName':'售价','attrValue':'合成价格，不应成为参数'}]}],
        'assets':[]}
    result.update(changes)
    return result


def picture(color='green'):
    stream=BytesIO();Image.new('RGB',(640,480),color).save(stream,'PNG');return stream.getvalue()


def asset(sku='11001',scope='sku_gallery',**changes):
    return {'id':'catalog-image-'+sku,'spu_id':'1001','sku_id':sku,'scope':scope,
        'image_url':'https://res.vmallres.com/pimages/'+sku+'.png','media_type':'image',**changes}


def test_parameters_keep_actual_remark_and_internal_sku_scope_in_writer_wire():
    snapshot=row();before=deepcopy(snapshot)
    bundle=vmall.parameter_evidence(snapshot)
    assert len(bundle.facts)==2 and bundle.verified_local
    screen=next(f for f in bundle.facts if f.label=='屏幕尺寸')
    assert screen.value=='6.75 英寸'
    assert screen.qualifiers==['适用规格：合成手机 Pro 8GB+128GB 黑色','*按标准矩形测量。\n实际可视区域略小。']
    assert screen.sku_scope=='vmall:1001:11001'
    assert all('11001' not in q for q in screen.qualifiers)
    assert screen.qualifiers[-1] in screen.quote
    assert bundle.sources[0].url==snapshot['source_url']
    assert bundle.sources[0].sha256==snapshot['source_sha256']
    assert bundle.sources[0].captured_at==snapshot['captured_at']
    assert bundle.sources[0].provider=='vmall_storefront'
    assert '合成品牌' in bundle.sources[0].title and '示例商家' in bundle.sources[0].title
    assert bundle.warnings==[]
    wire=OpenAICompatibleLLM(api_key='synthetic-unused')._context(bundle,[screen.id])
    assert next(f for f in wire['facts'] if f['id']==screen.id)['sku_scope']=='vmall:1001:11001'
    assert snapshot==before


@pytest.mark.parametrize('changes',[{'parameter_sku_id':''},{'parameter_sku_id':'999'},
    {'variants':[]},{'source_sha256':''},{'source_url':'https://www.vmall.com/product/999.html'},
    ])
def test_missing_or_mismatched_scope_never_borrows_first_variant(changes):
    assert vmall.parameter_evidence(row(**changes)).facts==[]


def test_empty_sections_read_only_actual_selected_variant_attributes_and_remark():
    snapshot=row(parameter_sections=[])
    snapshot['variants'][0]['attributes']=[{'attrName':'颜色','attrValue':'黑色','attrRemark':'表面颜色受光线影响。'},
        {'attrName':'存储容量','attrValue':'128GB'}]
    snapshot['variants'][1]['attributes']=[{'attrName':'存储容量','attrValue':'256GB'}]
    facts=vmall.parameter_evidence(snapshot).facts
    assert {f.value for f in facts}=={'黑色','128GB'}
    assert next(f for f in facts if f.label=='颜色').qualifiers[-1]=='表面颜色受光线影响。'
    assert all(f.sku_scope=='vmall:1001:11001' for f in facts)
    snapshot['parameter_sku_id']=''
    assert vmall.parameter_evidence(snapshot).facts==[]


def test_parameter_section_precedes_conflicting_variant_attribute():
    snapshot=row()
    snapshot['variants'][0]['attributes']=[{'attrName':'屏幕尺寸','attrValue':'7.0 英寸'},
        {'attrName':'颜色','attrValue':'黑色'}]
    facts=vmall.parameter_evidence(snapshot).facts
    assert [f.value for f in facts if f.label=='屏幕尺寸']==['6.75 英寸']
    assert any(f.value=='黑色' for f in facts)


def test_only_selected_variant_name_is_a_real_minimum_anchor_not_performance():
    snapshot=row(parameter_sections=[])
    facts=vmall.parameter_evidence(snapshot).facts
    assert len(facts)==1 and facts[0].label=='具体配置 / 商品名称'
    assert facts[0].value==snapshot['variants'][0]['name']
    assert facts[0].sku_scope=='vmall:1001:11001'
    assert not any('11001' in q for q in facts[0].qualifiers)
    assert vmall.catalog_entries(rows=[snapshot])[0]['fact_count']==1
    snapshot['parameter_sku_id']=''
    assert vmall.parameter_evidence(snapshot).facts==[]


def test_exact_unique_name_only_and_catalog_does_not_replace_legacy_names():
    snapshot=row()
    assert vmall.match_vmall_product('合成手机  Pro',rows=[snapshot])==snapshot
    assert vmall.match_vmall_product('合成手机',rows=[snapshot]) is None
    assert vmall.match_vmall_product(snapshot['name'],rows=[snapshot,deepcopy(snapshot)]) is None
    assert vmall.catalog_entries([snapshot['name']],rows=[snapshot])==[]
    catalog=vmall.catalog_entries(rows=[snapshot])
    assert catalog[0]['parameter_sku_id']=='11001' and catalog[0]['system']=='HarmonyOS 测试版'


def test_assets_bind_default_sku_and_common_detail_without_packshot_upgrade(tmp_path):
    fetched=[]
    async def fetch(url):fetched.append(url);return picture(), 'image/png'
    snapshot=row(assets=[asset(),asset('11002'),asset('',scope='product_detail'),
        asset(scope='product_video',media_type='video'),asset(spu_id='999')])
    assets,sources,paths=asyncio.run(vmall.prepare_assets(snapshot,snapshot['name'],fetcher=fetch,root=tmp_path))
    assert all('11002' not in url for url in fetched)
    assert len(fetched)==2
    # Equal bytes are delivered once even if a detail placement repeats them.
    assert len(assets)==1 and assets[0].kind=='official_gallery'
    assert assets[0].provenance=='vmall_storefront'
    assert 'packshot' not in assets[0].kind and assets[0].semantic_basis=='title_only'
    assert assets[0].sha256==hashlib.sha256(picture()).hexdigest()
    assert sources[0].sha256==snapshot['source_sha256']
    assert next(iter(paths.values())).is_relative_to(tmp_path/'runtime/official-assets/vmall')


def test_long_detail_is_never_a_product_cutout_or_photographic_sample(tmp_path):
    async def fetch(url):return picture(), 'image/png'
    assets,_,_=asyncio.run(vmall.prepare_assets(row(assets=[asset('',scope='product_detail')]),'合成手机 Pro',fetcher=fetch,root=tmp_path))
    assert assets[0].kind=='official_detail' and assets[0].visual_role=='feature_detail'
    assert '不是拍摄样张' in assets[0].claim_scope


def test_actual_digest_cache_and_get_restoration_do_not_trigger_remote_fetch(tmp_path,monkeypatch):
    calls=[]
    async def fetch(url):calls.append(url);return picture(),'image/png'
    snapshot=row(assets=[asset()]);before=deepcopy(snapshot)
    result=asyncio.run(vmall.prepare_assets(snapshot,snapshot['name'],fetcher=fetch,root=tmp_path))
    second=asyncio.run(vmall.prepare_assets(snapshot,snapshot['name'],fetcher=fetch,root=tmp_path))
    assert len(calls)==1 and snapshot==before
    identifier=result[0][0].id
    assert second[0][0].id==identifier
    monkeypatch.setattr(vmall,'ROOT',tmp_path)
    async def forbidden(url):raise AssertionError('GET may not download')
    monkeypatch.setattr(vmall,'download_asset',forbidden)
    provider=LocalKnowledgeProvider(root=tmp_path/'archive')
    path=asyncio.run(provider.get_asset(identifier))
    assert path.read_bytes()==picture()
    path.write_bytes(b'changed')
    assert asyncio.run(provider.get_asset(identifier)) is None


def test_expected_hash_mismatch_and_wrong_mime_never_register_asset(tmp_path):
    async def fetch(url):return picture(),'image/png'
    assert asyncio.run(vmall.prepare_assets(row(assets=[asset(sha256='0'*64)]),'合成',fetcher=fetch,root=tmp_path))[0]==[]
    async def wrong_mime(url):return picture(),'image/jpeg'
    assert asyncio.run(vmall.prepare_assets(row(assets=[asset()]),'合成',fetcher=wrong_mime,root=tmp_path))[0]==[]


def test_provider_uses_parameters_and_local_assets_for_new_exact_catalogue_product(tmp_path,monkeypatch):
    monkeypatch.setattr(vmall,'ROOT',tmp_path)
    monkeypatch.setattr(vmall,'load_vmall_products',lambda:[row(assets=[asset()])])
    async def fetch(url):return picture(),'image/png'
    monkeypatch.setattr(vmall,'download_asset',fetch)
    products=tmp_path/'products.json';products.write_text('{"products":[]}')
    images=tmp_path/'images.json';images.write_text('{"assets":[]}')
    provider=LocalKnowledgeProvider(root=tmp_path/'archive',official_products=products,official_assets=images,include_vmall=True)
    assert any(item['name']=='合成手机 Pro' for item in asyncio.run(provider.catalog()))
    bundle=asyncio.run(provider.retrieve('合成手机 Pro'))
    assert bundle.verified_local and bundle.facts and bundle.assets
    assert bundle.facts[0].sku_scope=='vmall:1001:11001'
    assert asyncio.run(provider.get_asset(bundle.assets[0].id)).read_bytes()==picture()


def test_existing_exact_knowledge_takes_precedence_without_cross_sku_assets(tmp_path,monkeypatch):
    products=tmp_path/'products.json'
    products.write_text(json.dumps({'products':[{'name':'合成手机 Pro','product':'合成手机 Pro',
        'spec_url':'https://consumer.huawei.com/cn/phones/test/specs/','facts':[
            {'label':'内存','value':'旧版8GB','quote':'旧版8GB','qualifiers':[]}]}]},ensure_ascii=False),encoding='utf-8')
    images=tmp_path/'images.json';images.write_text('{"assets":[]}')
    def forbidden(*args,**kwargs):raise AssertionError('Do not mix another storefront SKU')
    monkeypatch.setattr(vmall,'match_vmall_product',forbidden)
    provider=LocalKnowledgeProvider(root=tmp_path/'archive',official_products=products,official_assets=images,include_vmall=True)
    bundle=asyncio.run(provider.retrieve('合成手机 Pro'))
    assert len(bundle.facts)==1 and bundle.facts[0].value=='旧版8GB'
    assert not bundle.assets


def test_ambiguous_catalogue_product_never_fetches_or_attaches_assets(tmp_path,monkeypatch):
    monkeypatch.setattr(vmall,'load_vmall_products',lambda:[row(),row(spu_id='1002',catalog_key='vmall-product-1002',source_url='https://www.vmall.com/product/1002.html')])
    async def forbidden(url):raise AssertionError('Ambiguous identity cannot fetch')
    monkeypatch.setattr(vmall,'download_asset',forbidden)
    products=tmp_path/'products.json';products.write_text('{"products":[]}')
    images=tmp_path/'images.json';images.write_text('{"assets":[]}')
    bundle=asyncio.run(LocalKnowledgeProvider(root=tmp_path/'archive',official_products=products,official_assets=images,include_vmall=True).retrieve('合成手机 Pro'))
    assert not bundle.facts and not bundle.assets


def test_downloader_rejects_private_dns_before_http(monkeypatch):
    async def attempt():
        async def private(*args,**kwargs):return [(None,None,None,None,('127.0.0.1',443))]
        monkeypatch.setattr(asyncio.get_running_loop(),'getaddrinfo',private)
        with pytest.raises(ValueError,match='private_asset_host'):
            await vmall.download_asset('https://res.vmallres.com/pimages/test.png')
    asyncio.run(attempt())


def test_downloader_pins_public_ip_preserves_tls_host_and_refuses_redirect(monkeypatch):
    real_client=httpx.AsyncClient;requests=[]
    def handler(request):
        requests.append(request)
        return httpx.Response(302,headers={'Location':'https://res.vmallres.com/other.png'})
    def client(*args,**kwargs):return real_client(*args,transport=httpx.MockTransport(handler),**kwargs)
    monkeypatch.setattr(vmall.httpx,'AsyncClient',client)
    async def attempt():
        async def public(*args,**kwargs):return [(None,None,None,None,('93.184.216.34',443))]
        monkeypatch.setattr(asyncio.get_running_loop(),'getaddrinfo',public)
        with pytest.raises(ValueError,match='asset_redirect'):
            await vmall.download_asset('https://res.vmallres.com/pimages/test.png')
    asyncio.run(attempt())
    assert len(requests)==1 and requests[0].url.host=='93.184.216.34'
    assert requests[0].headers['Host']=='res.vmallres.com'
    assert requests[0].extensions['sni_hostname']=='res.vmallres.com'


@pytest.mark.parametrize('url',['http://res.vmallres.com/test.png','https://res.vmallres.com.evil.invalid/test.png','https://user:pass@res.vmallres.com/test.png'])
def test_downloader_rejects_unapproved_urls_without_network(url):
    with pytest.raises(ValueError,match='asset_url'):asyncio.run(vmall.download_asset(url))
