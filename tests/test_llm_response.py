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


def test_format_retry_preserves_budget_for_final_review(monkeypatch):
 model=client(monkeypatch,['{"step":"write"}','{"step":"review"}','bad',
                           '{"step":"repair"}','{"step":"final_review"}'],max_calls=6)
 async def stages():
  return [await model._json('JSON',{}) for _ in range(4)]
 assert [value['step'] for value in asyncio.run(stages())]==['write','review','repair','final_review']
 assert model._calls==5


def test_explicit_model_route_does_not_inherit_windows_system_proxy(monkeypatch):
 original=httpx.AsyncClient
 options=[]
 def build(**kwargs):
  options.append(kwargs.copy())
  def handle(request):
   return httpx.Response(200,json={'choices':[{'finish_reason':'stop',
    'message':{'content':'{"ok":true}'}}]})
  return original(transport=httpx.MockTransport(handle),**kwargs)
 monkeypatch.setattr(httpx,'AsyncClient',build)
 result=asyncio.run(OpenAICompatibleLLM(api_key='test-only')._json('JSON',{}))
 assert result=={'ok':True}
 assert options and all(item.get('trust_env') is False for item in options)
