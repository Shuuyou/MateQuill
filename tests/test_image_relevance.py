"""Image identity, scene relevance and visual truth are separate checks."""
import asyncio
import hashlib
import json
from types import SimpleNamespace

from app.facts import asset_scene_match, asset_semantics, rank_assets_for_scene
from app.ima import ImaProvider
from app.models import Asset
from app import visual_assets


def test_product_overview_does_not_support_unrelated_feature():
    meta=asset_semantics('高质感主视觉 / 多彩配色外观')
    assert meta['visual_role']=='product_overview'
    assert meta['semantic_basis']=='title_only'
    assert asset_scene_match('高质感主视觉','多屏协同连接手机与电脑')['score']==0
    assert asset_scene_match('轻薄精致机身','电池续航测试')['score']==0


def test_specific_scene_asset_is_ranked_before_generic_product_identity():
    assets=[{'id':'cover','title':'多彩配色外观'},
            {'id':'screen','title':'柔光屏幕触控展示'},
            {'id':'logo','title':'品牌 logo'}]
    result=rank_assets_for_scene(assets,'图书馆阅读，关心柔光屏幕')
    assert [item['id'] for item in result]==['screen','cover','logo']
    assert result[0] is assets[1]  # Stable provenance; no synthetic replacements.
    assert asset_scene_match('柔光屏幕触控展示','图书馆阅读，关心柔光屏幕')['matched_tags']==['display']


def test_unknown_title_is_not_upgraded_to_a_scene_or_feature():
    meta=asset_semantics('hwpc_test_01_abcd.png')
    assert meta['visual_role']=='unknown'
    assert meta['semantic_tags']==[]
    assert asset_scene_match('hwpc_test_01_abcd.png','毕业论文与显卡渲染')['score']==0


def test_ranking_accepts_asset_models_and_keeps_source_binding():
    a=Asset(id='one',product='Test',url='/one',source_id='source-one',title='多彩配色外观')
    b=Asset(id='two',product='Test',url='/two',source_id='source-two',title='键盘和触控板特写')
    result=rank_assets_for_scene([a,b],'键盘打字')
    assert result[0] is b
    assert result[0].source_id=='source-two'


def test_retrieve_prioritizes_scene_before_read_budget_and_removes_duplicate_bytes():
    product='TestBook 2026'
    provider=ImaProvider(SimpleNamespace(ima_ready=True,ima_client_id='synthetic',
        ima_api_key='synthetic',ima_kb_name='synthetic',max_media_bytes=1024,max_directory_items=100))
    provider.kb_id='synthetic'
    provider.asset_locators=lambda product:{}
    names=[f'外观配色_{n}.png' for n in range(13)]+['键盘触控板特写.png','品牌logo.png']
    async def call(endpoint,body):
        return {'info_list':[]}
    async def fact_document(product):
        return None
    async def index(force=False,product=''):
        return [{'media_id':'doc','title':'article.md','path':f'P1-产品参数库/{product}/article.md'}]+[
            {'media_id':str(n),'title':title,'path':f'P1-产品参数库/{product}/{title}'}
            for n,title in enumerate(names)]
    requested=[]
    async def media(row):
        requested.append(row['title'])
        if row['media_id']=='doc':
            return '内存：16GB'.encode(),'text/plain'
        suffix=b'keyboard' if '键盘' in row['title'] else b'overview'
        return b'\x89PNG\r\n\x1a\n'+suffix,'image/png'
    provider.call=call
    provider.fact_document=fact_document
    provider.index=index
    provider.media=media
    result=asyncio.run(provider.retrieve(product,'长时间写论文，键盘与触控板输入体验'))
    assert requested[1]=='键盘触控板特写.png'
    assert '品牌logo.png' not in requested
    assert len(result.assets)==2  # Repeated identical bytes cannot pad a carousel.
    assert result.assets[0].title=='键盘触控板特写.png'
    assert result.assets[0].semantic_basis=='title_only'
    assert result.assets[0].source_id in {source.id for source in result.sources}


def test_pixel_review_overrides_misleading_filename_without_certifying_identity(tmp_path,monkeypatch):
    raw=b'synthetic reviewed image'
    digest=hashlib.sha256(raw).hexdigest()
    path=tmp_path/'reviews.json'
    path.write_text(json.dumps({digest:{'observed_title':'合盖金色笔记本机身',
        'visual_role':'product_overview','semantic_tags':['appearance'],'usable':True,
        'visual_group':'golden-closed-front','reason':'画面未见屏幕'}}),encoding='utf-8')
    monkeypatch.setattr(visual_assets,'REVIEW_PATH',path)
    result=visual_assets.review_asset_bytes(raw,'高清护眼屏.jpg')
    assert result['title']=='合盖金色笔记本机身'
    assert result['original_title']=='高清护眼屏.jpg'
    assert result['semantic_tags']==['appearance']
    assert result['semantic_basis']=='pixel_review_sha256'
    assert result['visual_group']=='golden-closed-front'
    assert '型号' in result['claim_scope'] and '授权' in result['claim_scope']
    # Even a one-byte change must invalidate the exact-pixel observation.
    unknown=visual_assets.review_asset_bytes(raw+b'!','高清护眼屏.jpg')
    assert unknown['semantic_basis']=='title_only'
    assert unknown['visual_review_warning']
    assert unknown['visual_group']==''


def test_pixel_review_registry_can_reject_unusable_asset(tmp_path,monkeypatch):
    raw=b'synthetic unsuitable image'
    path=tmp_path/'reviews.json'
    path.write_text(json.dumps({'reviews':{hashlib.sha256(raw).hexdigest():{
        'observed_title':'二维码与无关品牌标识','visual_role':'decorative',
        'semantic_tags':[],'usable':False,'reason':'不是产品场景图'}}}),encoding='utf-8')
    monkeypatch.setattr(visual_assets,'REVIEW_PATH',path)
    result=visual_assets.review_asset_bytes(raw,'多屏协同.jpg')
    assert result['usable'] is False
    assert result['reason']=='不是产品场景图'


def test_unknown_or_malformed_review_never_claims_pixel_verification(tmp_path,monkeypatch):
    path=tmp_path/'reviews.json'
    monkeypatch.setattr(visual_assets,'REVIEW_PATH',path)
    assert visual_assets.review_asset_bytes(b'raw','外观.jpg')['semantic_basis']=='title_only'
    path.write_text('{bad json',encoding='utf-8')
    assert visual_assets.review_asset_bytes(b'raw','外观.jpg')['visual_review_warning']


def test_retrieve_drops_known_bad_pixels_and_uses_observed_title(tmp_path,monkeypatch):
    raw_bad=b'\x89PNG\r\n\x1a\nknown-bad'
    raw_good=b'\x89PNG\r\n\x1a\nknown-good'
    registry={hashlib.sha256(raw).hexdigest():{'observed_title':title,
        'visual_role':'product_overview','semantic_tags':['appearance'],'usable':usable,'reason':'synthetic review'}
        for raw,title,usable in [(raw_bad,'不适用画面',False),(raw_good,'合盖机身外观',True)]}
    path=tmp_path/'reviews.json'
    path.write_text(json.dumps(registry),encoding='utf-8')
    monkeypatch.setattr(visual_assets,'REVIEW_PATH',path)
    provider=ImaProvider(SimpleNamespace(ima_ready=True,ima_client_id='test',
        ima_api_key='test',ima_kb_name='test',max_media_bytes=1024,max_directory_items=100))
    provider.kb_id='test'
    provider.asset_locators=lambda product:{}
    async def call(endpoint,body): return {'info_list':[]}
    async def fact_document(product): return None
    async def index(force=False,product=''):
        return [{'media_id':mid,'title':title,'path':f'P1-产品参数库/TestBook/{title}'}
            for mid,title in [('doc','article.md'),('bad','多屏协同.png'),('good','高清护眼屏.png')]]
    async def media(row):
        if row['media_id']=='doc': return '内存：16GB'.encode(),'text/plain'
        return (raw_bad if row['media_id']=='bad' else raw_good),'image/png'
    provider.call=call
    provider.fact_document=fact_document
    provider.index=index
    provider.media=media
    bundle=asyncio.run(provider.retrieve('TestBook','屏幕阅读'))
    assert len(bundle.assets)==1
    assert bundle.assets[0].title=='合盖机身外观'
    assert bundle.assets[0].semantic_basis=='pixel_review_sha256'
    assert bundle.assets[0].verified  # Existing source check stays separate.
    assert any('像素审阅已排除' in warning for warning in bundle.warnings)
    assert len(provider.assets)==1


def test_retrieve_deduplicates_reviewed_visual_groups_for_different_bytes(tmp_path,monkeypatch):
    raw_images={
        'a': b'\x89PNG\r\n\x1a\nsmall-view',
        'b': b'\x89PNG\r\n\x1a\nlarge-view',
        'c': b'\x89PNG\r\n\x1a\nother-view',
        'd': b'\x89PNG\r\n\x1a\nother-view',
    }
    registry={hashlib.sha256(raw).hexdigest():{'observed_title':'笔记本机身外观',
        'visual_role':'product_overview','semantic_tags':['appearance'],'usable':True,
        'visual_group':'front-view' if mid in ('a','b') else '', 'reason':'synthetic review'}
        for mid,raw in raw_images.items()}
    path=tmp_path/'reviews.json'
    path.write_text(json.dumps(registry),encoding='utf-8')
    monkeypatch.setattr(visual_assets,'REVIEW_PATH',path)
    provider=ImaProvider(SimpleNamespace(ima_ready=True,ima_client_id='test',
        ima_api_key='test',ima_kb_name='test',max_media_bytes=1024,max_directory_items=100))
    provider.kb_id='test'
    provider.asset_locators=lambda product:{}
    async def call(endpoint,body): return {'info_list':[]}
    async def fact_document(product): return None
    async def index(force=False,product=''):
        return [{'media_id':'doc','title':'article.md','path':'P1-产品参数库/TestBook/article.md'}]+[
            {'media_id':mid,'title':mid+'.png','path':f'P1-产品参数库/TestBook/{mid}.png'}
            for mid in raw_images]
    async def media(row):
        if row['media_id']=='doc': return '内存：16GB'.encode(),'text/plain'
        return raw_images[row['media_id']],'image/png'
    provider.call=call
    provider.fact_document=fact_document
    provider.index=index
    provider.media=media
    bundle=asyncio.run(provider.retrieve('TestBook','外观'))
    assert len(bundle.assets)==2  # a/b share one scene; c/d share exact bytes.
    assert sum(asset.visual_group=='front-view' for asset in bundle.assets)==1
    assert all(asset.product=='TestBook' for asset in bundle.assets)

