import asyncio
import base64

from fastapi.testclient import TestClient

from app import api
from app.creative_export import render_creative_html, embed_card_assets


def test_creative_catalog_and_brief_are_reusable_without_model(monkeypatch):
    monkeypatch.setattr(api.settings,'llm_api_key','')
    client=TestClient(api.app)
    response=client.get('/api/creative/connectors')
    assert response.status_code==200
    items=response.json()['items']
    assert {x['platform'] for x in items}=={'xiaohongshu','wechat','moments','poster'}
    assert all(x.get('id') and x.get('version') for x in items)
    response=client.post('/api/creative/brief',json={'product':'TestBook','audience':'学生','scene':'去图书馆整理资料','platform':'xiaohongshu'})
    assert response.status_code==200
    assert response.json()['model_called'] is False
    assert response.json()['evidence_status']=='not_retrieved'
    assert client.post('/api/creative/brief',json={'platform':'xiaohongshu','creative_connector':'missing-connector'}).status_code==422
    assert client.post('/api/run',json={'platform':'xiaohongshu','creative_connector':'missing-connector'}).status_code==422
    assert client.post('/api/run',json={'platform':'unregistered-platform'}).status_code==422


def test_card_html_escapes_all_copy_and_keeps_public_disclosures():
    injection='<script>alert(1)</script>'
    record={'status':'draft','title':injection,'intro':injection,'blocks':[{'text':injection,'disclosures':['仅限指定版本']}],
        'assets':[{'id':'a1','title':injection,'url':'https://example.invalid/?token=SECRET'}],
        'creative_plan':{'connector_id':'xhs','connector_version':'1','cards':[{'id':'s1','headline':injection,'body':injection,
            'role':'cover','asset_ids':['a1'],'disclosures':['需支持的设备']} ]}}
    rendered=render_creative_html(record)
    assert '<script>' not in rendered and '&lt;script&gt;' in rendered
    assert 'SECRET' not in rendered and 'example.invalid' not in rendered
    assert '仅限指定版本' in rendered and '需支持的设备' in rendered
    assert '草稿' in rendered and '素材未嵌入' in rendered


def test_embedded_html_assets_survive_download_without_external_url(monkeypatch):
    from fastapi.responses import Response
    raw=b'\x89PNG\r\n\x1a\nsynthetic-image'
    called=[]
    async def image(aid):
        called.append(aid)
        return Response(raw,media_type='image/png')
    monkeypatch.setattr(api,'asset',image)
    record={'status':'draft','assets':[{'id':'a1','title':'合成素材'}],
        'creative_plan':{'cards':[{'id':'s1','headline':'任务卡','asset_ids':['a1','untrusted-id']}]}}
    embedded=asyncio.run(embed_card_assets(record))
    assert called==['a1']
    rendered=render_creative_html(record,embedded)
    assert 'data:image/png;base64,'+base64.b64encode(raw).decode() in rendered
    assert 'src="/api/' not in rendered


def test_blocked_creative_export_remains_forbidden():
    api.runs['creative-blocked']={'status':'blocked','creative_plan':{'cards':[{'headline':'不能发布'}]}}
    try:
        assert TestClient(api.app).get('/api/runs/creative-blocked/export?format=html').status_code==409
    finally:
        api.runs.pop('creative-blocked',None)
