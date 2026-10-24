import asyncio
import base64
import hashlib
import io
import json

import httpx
import pytest
from PIL import Image

from app.design_review import review_design_element, review_with_model, accepted_design_element_review

CATEGORIES=('product_or_device','people_or_body_parts','documentary_scene','text_or_logo')


def plate():
    stream=io.BytesIO();Image.new('RGB',(40,40),'#102030').save(stream,'PNG');raw=stream.getvalue()
    return {'provenance':'ai_generated_design_elements','data_url':'data:image/png;base64,'+base64.b64encode(raw).decode(),
        'sha256':hashlib.sha256(raw).hexdigest(),'width':40,'height':40,
        'final_composite_data_url':'FINAL-REAL-PRODUCT-MUST-NOT-BE-SENT'}


def wire(status='absent'):
    checks={key:{'status':'absent','region':'','observation':'Actual abstract material without '+key} for key in CATEGORIES}
    if status!='absent':checks['product_or_device']={'status':status,'region':'center lower area',
        'observation':'A rounded phone-like body with a camera module is visible'}
    return {'review_completed':True,'checks':checks}


def mock_client(monkeypatch,handler):
    actual=httpx.AsyncClient
    def client(**kwargs):return actual(transport=httpx.MockTransport(handler),**kwargs)
    monkeypatch.setattr(httpx,'AsyncClient',client)


@pytest.mark.parametrize('status,action',[('absent','accept'),('present','reject'),('uncertain','incomplete')])
def test_isolated_plate_review_server_derives_gate_and_never_sends_final_real_products(monkeypatch,status,action):
    metadata=plate();captured=[]
    def handler(request):
        body=json.loads(request.content);captured.append(body)
        assert 'FINAL-REAL-PRODUCT' not in json.dumps(body)
        images=[c for c in body['messages'][1]['content'] if c['type']=='image_url']
        assert len(images)==1 and images[0]['image_url']['url']==metadata['data_url']
        assert body['reasoning_effort']=='low' and body['max_completion_tokens']>=9000
        assert 'max_tokens' not in body and 'temperature' not in body
        assert body['response_format']['type']=='json_schema'
        assert 'NOT a final advertisement' in body['messages'][0]['content']
        return httpx.Response(200,json={'choices':[{'message':{'content':json.dumps(wire(status))}}]})
    mock_client(monkeypatch,handler)
    result=asyncio.run(review_design_element(metadata,'https://vision.example/v1','CANARY','gpt-6-astra'))
    assert len(captured)==1 and result['review_completed'] is True and result['action']==action
    assert result['reviewed_artifact_sha256']==metadata['sha256']
    assert result['review_scope']=='standalone_design_element' and len(result['checks'])==4
    assert 'CANARY' not in json.dumps(result)


def test_default_model_wire_unchanged_and_ambiguous_missing_observation_does_not_pass(monkeypatch):
    calls=[]
    def handler(request):
        body=json.loads(request.content);calls.append(body)
        assert body['max_tokens']==2400 and body['temperature']==.05
        assert 'reasoning_effort' not in body and 'max_completion_tokens' not in body
        broken=wire();del broken['checks']['text_or_logo']
        return httpx.Response(200,json={'choices':[{'message':{'content':json.dumps(broken)}}]})
    mock_client(monkeypatch,handler)
    result=asyncio.run(review_design_element(plate(),'https://vision.example/v1','CANARY','vl'))
    assert result['action']=='incomplete' and result['review_completed'] is False and len(calls)==1


@pytest.mark.parametrize('mutation',[
    lambda p:p.update(sha256='0'*64),lambda p:p.update(provenance='final_composite'),
    lambda p:p.update(width=41),lambda p:p.update(data_url='https://private.invalid/image.png')])
def test_invalid_or_nonplate_image_never_launches_vision_request(monkeypatch,mutation):
    def forbidden(**kwargs):pytest.fail('invalid input must not invoke vision')
    monkeypatch.setattr(httpx,'AsyncClient',forbidden)
    metadata=plate();mutation(metadata)
    result=asyncio.run(review_design_element(metadata,'https://vision.example/v1','CANARY','vl'))
    assert result['review_completed'] is False and result['action']=='incomplete'


def test_only_explicit_schema_capability_rejection_has_one_compatibility_retry(monkeypatch):
    calls=[]
    def handler(request):
        body=json.loads(request.content);calls.append(body)
        if len(calls)==1:return httpx.Response(400,json={'error':{'message':'response_format json_schema is not supported'}})
        assert body['response_format']=={'type':'json_object'}
        return httpx.Response(200,json={'choices':[{'message':{'content':json.dumps(wire())}}]})
    mock_client(monkeypatch,handler)
    result=asyncio.run(review_design_element(plate(),'https://vision.example/v1','CANARY','gpt-6-astra'))
    assert result['action']=='accept' and len(calls)==2 and result['response_format_used']=='json_object'


def test_present_content_without_pixel_region_is_incomplete_not_clean(monkeypatch):
    def handler(request):
        review=wire('present');review['checks']['product_or_device']['region']=''
        return httpx.Response(200,json={'choices':[{'message':{'content':json.dumps(review)}}]})
    mock_client(monkeypatch,handler)
    result=asyncio.run(review_design_element(plate(),'https://vision.example/v1','CANARY','vl'))
    assert result['action']=='incomplete' and result['review_completed'] is False


def test_existing_final_visual_review_uses_compatible_gpt6_reasoning_wire(monkeypatch):
    from test_design_review import doc,render
    document=doc();png=render(document)
    def handler(request):
        body=json.loads(request.content)
        assert body['reasoning_effort']=='low' and body['max_completion_tokens']>=9000
        assert 'temperature' not in body and 'max_tokens' not in body
        review={'review_completed':True,'score':88,'edits':[],
            'checks':[{'category':key,'severity':'info','message':'Observed actual pixels: '+key}
                      for key in ('composition','typography','imagery','platform','specificity')]}
        return httpx.Response(200,json={'choices':[{'message':{'content':json.dumps(review)}}]})
    mock_client(monkeypatch,handler)
    result=asyncio.run(review_with_model(png,document,'https://vision.example/v1','CANARY','gpt-6-astra'))
    assert result['review_completed'] is True and result['score']==88


def test_cached_plate_acceptance_is_bound_to_sha_protocol_scope_and_action():
    digest='a'*64
    review={'review_completed':True,'action':'accept','protocol_version':1,
            'review_scope':'standalone_design_element','reviewed_artifact_sha256':digest}
    assert accepted_design_element_review(review,digest)
    assert not accepted_design_element_review(review,'b'*64)
    assert not accepted_design_element_review(review,'bad-digest')
    for changes in ({'review_completed':False},{'action':'reject'},{'action':'incomplete'},
                    {'action':'deliver'},{'protocol_version':2},{'protocol_version':True},
                    {'review_scope':'final_composite'},{'reviewed_artifact_sha256':'b'*64}):
        assert not accepted_design_element_review({**review,**changes,'score':100},digest)
    assert not accepted_design_element_review({'score':100,'decision':'accept'},digest)
