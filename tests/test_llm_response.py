import asyncio,json
import httpx
import pytest
from app.llm import OpenAICompatibleLLM,ModelCallError


def client(monkeypatch,contents,max_calls=4):
 original=httpx.AsyncClient
 replies=iter(contents)
 def handle(request):
  value=next(replies)
  if isinstance(value,dict):return httpx.Response(200,json=value)
  return httpx.Response(200,json={'choices':[{'finish_reason':'stop','message':{'content':value}}],'usage':{'completion_tokens':5}})
 monkeypatch.setattr(httpx,'AsyncClient',lambda **kw:original(transport=httpx.MockTransport(handle),**kw))
 return OpenAICompatibleLLM(api_key='test-only',max_calls=max_calls)

@pytest.mark.parametrize('text',['{"audit": []}','```json\n{"audit": []}\n```','\ufeff{"audit": []}'])
def test_complete_object_and_exact_fence(monkeypatch,text):
 model=client(monkeypatch,[text])
 assert asyncio.run(model._json('Return JSON',{}))=={'audit':[]}
 assert len(model.metrics)==1

@pytest.mark.parametrize('bad',['', '{"title":"unfinished', 'Here is your answer: {"audit": []}', '[]'])
def test_bad_content_retries_original_request_once(monkeypatch,bad):
 model=client(monkeypatch,[bad,'{"audit": []}'])
 assert asyncio.run(model._json('Return JSON',{}))=={'audit':[]}
 assert len(model.metrics)==2
 assert model.metrics[0]['outcome'] in {'invalid_model_json','empty_model_content'}
 assert model.metrics[1]['outcome']=='success'

def test_persistent_bad_response_fails_with_bounded_calls(monkeypatch):
 model=client(monkeypatch,['bad','bad'])
 with pytest.raises(ModelCallError,match='invalid_model_json'):asyncio.run(model._json('JSON',{}))
 assert model._calls==2

def test_retry_respects_remaining_call_budget(monkeypatch):
 model=client(monkeypatch,['bad'],max_calls=1)
 with pytest.raises(ModelCallError,match='invalid_model_json'):asyncio.run(model._json('JSON',{}))
 assert model._calls==1

def test_invalid_envelope_is_distinct_from_content(monkeypatch):
 model=client(monkeypatch,[{'choices':[]}])
 with pytest.raises(ModelCallError,match='invalid_model_response'):asyncio.run(model._json('JSON',{}))
 assert len(model.metrics)==1
