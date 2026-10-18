import asyncio
import base64
import json

import httpx
import pytest

from app.models import TaskInput, RunResult, Asset, ContentBlock
from app.poster import PosterBrief, build_poster
from app import poster_critic

PNG=base64.b64decode('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+jO1sAAAAASUVORK5CYII=')


def doc():
    task=TaskInput(product='PC',audience='学生',scene='课堂')
    result=RunResult(title='让课堂灵感流动',intro='选择适合你的课程任务',cta='咨询软件需求',
        blocks=[ContentBlock(text='现场验证所需工作流')],
        assets=[Asset(id='a',product='PC',url='https://secret.local/token',source_id='s',verified=True)])
    return build_poster(task,result,PosterBrief())


def good_review(edits=None):
    return {'checks':[{'category':category,'severity':'info','message':'实际PNG中该项内容清晰可见：'+category}
                      for category in poster_critic.CATEGORIES],
            'notes':['小尺寸中文仍建议人工确认'],'safe_layer_edits':edits or []}


def setup_mock(monkeypatch, handler):
    actual_client=httpx.AsyncClient
    transport=httpx.MockTransport(handler)
    monkeypatch.setattr(poster_critic.httpx,'AsyncClient',lambda **kwargs:actual_client(transport=transport,**kwargs))


def run(document=None,**kwargs):
    return asyncio.run(poster_critic.review_poster(PNG,document or doc(),
        kwargs.get('base_url','https://model.example/v1'),kwargs.get('api_key','test-key'),model='test-vl'))


def test_actual_image_multimodal_payload_and_success(monkeypatch):
    def handler(request):
        assert str(request.url)=='https://model.example/v1/chat/completions'
        assert request.headers['authorization']=='Bearer test-key'
        payload=json.loads(request.content)
        assert payload['model']=='test-vl'
        content=payload['messages'][1]['content']
        assert content[1]['type']=='image_url'
        assert base64.b64decode(content[1]['image_url']['url'].split(',')[1])==PNG
        assert 'secret.local' not in json.dumps(payload)
        return httpx.Response(200,json={'choices':[{'message':{'content':json.dumps(good_review())}}]})
    setup_mock(monkeypatch,handler)
    result=run()
    assert len(result)==6
    status=result[-1]
    assert status['review_completed'] and status['api_call_success']
    assert '不能认证' in status['notes'][-1]


def test_safe_patch_suggestions_exclude_facts_and_locked_layers(monkeypatch):
    document=doc()
    title=next(layer for layer in document.layers if layer.role=='headline')
    product=next(layer for layer in document.layers if layer.role=='product')
    edits=[{'id':title.id,'changes':{'color':'#234567'}},
           {'id':title.id,'changes':{'text':'改写事实'}},
           {'id':product.id,'changes':{'x':0}},
           {'id':title.id,'changes':{'y':product.y}}]
    setup_mock(monkeypatch,lambda request:httpx.Response(200,json={'choices':[{'message':{'content':json.dumps(good_review(edits))}}]}))
    result=run(document)
    assert result[-1]['safe_layer_edits']==[edits[0]]
    assert '3项' in result[-1]['notes'][-2]
    assert next(layer for layer in document.layers if layer.id==title.id).color!= '#234567'


@pytest.mark.parametrize('content',['not json',json.dumps({'checks':[]}),json.dumps({'checks':[{'category':'fake'}]})])
def test_invalid_review_never_passes(monkeypatch,content):
    setup_mock(monkeypatch,lambda request:httpx.Response(200,json={'choices':[{'message':{'content':content}}]}))
    status=run()[-1]
    assert status['api_call_success'] and not status['review_completed']
    assert status['severity']=='warning'


def test_timeout_is_incomplete_not_pass(monkeypatch):
    def handler(request):
        raise httpx.ReadTimeout('secret-key-must-not-leak',request=request)
    setup_mock(monkeypatch,handler)
    result=run()
    assert not result[-1]['review_completed'] and not result[-1]['api_call_success']
    assert 'secret-key' not in str(result)


def test_http_failure_is_sanitized(monkeypatch):
    setup_mock(monkeypatch,lambda request:httpx.Response(401,text='test-key do not print'))
    result=run()
    assert '401' in result[-1]['message']
    assert 'test-key' not in str(result)
    assert not result[-1]['api_call_success']


def test_early_validation_never_calls_provider(monkeypatch):
    monkeypatch.setattr(poster_critic.httpx,'AsyncClient',lambda **kwargs:pytest.fail('network must not be called'))
    assert not run(api_key='')[-1]['review_completed']
    assert not run(base_url='http://model.example/v1')[-1]['review_completed']
    assert not run(base_url='https://token@model.example/v1')[-1]['review_completed']
    output=asyncio.run(poster_critic.review_poster(b'not png',doc(),'https://model.example/v1','key'))
    assert not output[-1]['review_completed']
