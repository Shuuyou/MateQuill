import asyncio
from types import SimpleNamespace

import httpx
import pytest

from app.ima import ImaError, ImaProvider


def provider():
    instance = ImaProvider(SimpleNamespace(ima_ready=True, ima_client_id="test-client", ima_api_key="CANARY_SECRET", ima_kb_name="test-kb", max_media_bytes=1024, max_directory_items=100))
    async def public_resolver(host):
        return ["93.184.216.34"]
    instance._resolve_public = public_resolver
    return instance


def install_transport(monkeypatch, handler):
    original = httpx.AsyncClient
    monkeypatch.setattr(httpx, "AsyncClient", lambda **kwargs: original(transport=httpx.MockTransport(handler), **kwargs))


@pytest.mark.parametrize("url", ["https://127.0.0.1/x", "http://ima.qq.com/x", "https://169.254.169.254/latest", "https://ima.qq.com.evil.invalid/x", "https://user:secret@ima.qq.com/x", "https://ima.qq.com:8443/x", "https://[::1]/x"])
def test_private_or_untrusted_asset_urls_rejected(url):
    with pytest.raises((ImaError, ValueError)):
        ImaProvider._safe_url(url)


def test_redirect_to_internal_address_is_rejected_before_request(monkeypatch):
    requests = []
    def handler(request):
        requests.append(request)
        return httpx.Response(302, headers={"location": "https://127.0.0.1/private"})
    install_transport(monkeypatch, handler)
    with pytest.raises(ImaError):
        asyncio.run(provider().read_url("https://ima.qq.com/image"))
    assert len(requests) == 1


def test_cross_host_redirect_never_forwards_credentials(monkeypatch):
    requests = []
    def handler(request):
        requests.append(request)
        if len(requests) == 1:
            return httpx.Response(302, headers={"location": "https://consumer.huawei.com/a.png"})
        return httpx.Response(200, content=b"image", headers={"content-type": "image/png"})
    install_transport(monkeypatch, handler)
    value = asyncio.run(provider().read_url("https://ima.qq.com/image", {"Authorization": "CANARY_SECRET", "ima-openapi-apikey": "CANARY_SECRET"}))
    assert value == (b"image", "image/png")
    assert "CANARY_SECRET" not in str(requests[1].headers)


def test_http_200_business_failure_is_not_success_or_secret_leak(monkeypatch):
    install_transport(monkeypatch, lambda request: httpx.Response(200, json={"code": 401, "msg": "CANARY_SECRET https://ima.qq.com/?token=CANARY_SECRET", "data": {"info_list": []}}))
    with pytest.raises(ImaError) as caught:
        asyncio.run(provider().call("search_knowledge", {}))
    assert "CANARY_SECRET" not in str(caught.value)


def test_directory_walk_consumes_pagination_and_folder_ids():
    instance = provider()
    calls = []
    async def call(endpoint, body):
        calls.append((endpoint, body))
        if endpoint == "search_knowledge_base":
            return {"info_list": [{"kb_id": "kb1", "kb_name": "test-kb"}], "is_end": True}
        if body.get("folder_id"):
            return {"knowledge_list": [{"media_id": "doc1", "title": "article.md"}], "is_end": True}
        if body.get("cursor") == "p2":
            return {"knowledge_list": [{"folder_id": "folder1", "title": "P1-产品参数库", "type": "folder"}], "is_end": True}
        return {"knowledge_list": [{"media_id": "image1", "title": "product.png"}], "is_end": False, "next_cursor": "p2"}
    instance.call = call
    result = asyncio.run(instance.index())
    assert any(row["path"] == "P1-产品参数库/article.md" for row in result)
    assert any(body.get("cursor") == "p2" for _, body in calls)
    assert any(body.get("folder_id") == "folder1" for _, body in calls)


def test_media_size_limit_is_enforced(monkeypatch):
    install_transport(monkeypatch, lambda request: httpx.Response(200, content=b"x" * 1025))
    with pytest.raises(ImaError):
        asyncio.run(provider().read_url("https://ima.qq.com/large"))


def test_empty_search_falls_back_to_exact_model_directory():
    instance = provider()
    instance.kb_id = "kb1"
    product = "TestBook Pro 2026"
    async def call(endpoint, body):
        assert endpoint == "search_knowledge"
        return {"info_list": [], "is_end": True}
    async def index(force=False, product=''):
        return [
            {"media_id": "doc1", "title": "article.md", "path": f"P1-产品参数库/{product}/article.md"},
            {"media_id": "img1", "title": "product.png", "path": f"P1-产品参数库/{product}/product.png"},
        ]
    async def media(row):
        if row["media_id"] == "doc1":
            return "内存：16GB".encode(), "text/plain"
        return b"\x89PNG\r\n\x1a\nsynthetic", "image/png"
    instance.call, instance.index, instance.media = call, index, media
    bundle = asyncio.run(instance.retrieve(product, "学生学习"))
    assert bundle.facts[0].value == "16GB"
    assert bundle.verified_live is True
    assert bundle.assets[0].verified is True


def test_invalid_image_payload_is_not_verified_as_a_product_image():
    instance = provider()
    instance.kb_id = "kb1"
    async def call(endpoint, body):
        return {"info_list": []}
    async def index(force=False, product=''):
        return [{"media_id": "doc", "title": "article.md", "path": "P1-产品参数库/TestBook/article.md"}, {"media_id": "img", "title": "product.png", "path": "P1-产品参数库/TestBook/product.png"}]
    async def media(row):
        return ("内存：16GB".encode(), "text/plain") if row["media_id"] == "doc" else (b"<html>not an image</html>", "image/png")
    instance.call, instance.index, instance.media = call, index, media
    bundle = asyncio.run(instance.retrieve("TestBook", "学习"))
    assert not bundle.assets
    assert bundle.verified_live  # Fact verification is separate from final image readiness.


def test_resolver_rejects_mixed_public_and_private_answers(monkeypatch):
    async def scenario():
        loop = asyncio.get_running_loop()
        async def answers(*args, **kwargs):
            return [(2, 1, 6, '', ('93.184.216.34', 443)), (2, 1, 6, '', ('127.0.0.1', 443))]
        monkeypatch.setattr(loop, 'getaddrinfo', answers)
        with pytest.raises(ImaError):
            await ImaProvider()._resolve_public('ima.qq.com')
    asyncio.run(scenario())


def test_media_connection_is_pinned_to_checked_ip_with_original_host_and_sni(monkeypatch):
    requests = []
    def handler(request):
        requests.append(request)
        return httpx.Response(200, content=b'ok', headers={'content-type':'image/png'})
    install_transport(monkeypatch, handler)
    asyncio.run(provider().read_url('https://ima.qq.com/a.png'))
    assert requests[0].url.host == '93.184.216.34'
    assert requests[0].headers['host'] == 'ima.qq.com'
    assert requests[0].extensions['sni_hostname'] == 'ima.qq.com'


def test_configured_knowledge_base_id_skips_search_discovery():
    instance = provider()
    instance.config.ima_kb_id = 'authorized-private-kb'
    async def forbidden_call(*args, **kwargs):
        raise AssertionError('Configured ID must not consume discovery API quota')
    instance.call = forbidden_call
    assert asyncio.run(instance.connect()) == 'authorized-private-kb'


def test_configured_base_probe_only_uses_search():
    instance = provider()
    instance.config.ima_kb_id = 'authorized-private-kb'
    calls = []
    async def call(endpoint, body):
        calls.append(endpoint)
        assert endpoint == 'search_knowledge'
        return {'info_list': [], 'is_end': True}
    instance.call = call
    result = asyncio.run(instance.probe())
    assert result['ok'] and result['route'] == 'search_knowledge'
    assert calls == ['search_knowledge']


def flat_document_provider(text):
    instance = provider()
    instance.kb_id = 'kb1'
    async def call(endpoint, body):
        assert endpoint == 'search_knowledge'
        return {'info_list':[{'media_id':'doc1','title':'TestBook Pro 2026.md'}]}
    async def quota_directory(*args, **kwargs):
        raise AssertionError('Exact search match must not require directory quota')
    async def media(row):
        return text.encode(), 'text/markdown'
    instance.call, instance.index, instance.media = call, quota_directory, media
    return instance


def test_exact_flat_document_search_does_not_call_exhausted_directory():
    instance = flat_document_provider('# TestBook Pro 2026 - 产品事实\n## 内存\n16GB\n## 操作系统\nHarmonyOS\n')
    result = asyncio.run(instance.retrieve('TestBook Pro 2026', '课堂学习'))
    assert result.facts and result.sources
    assert result.verified_live  # Facts verified; the workflow must still reject missing images.


def test_flat_discovery_card_is_not_promoted_to_facts():
    instance = flat_document_provider('# TestBook Pro 2026\n## 官网公开定位摘要\n内存：16GB\n## 建库状态\n尚待抓取完整参数\n')
    result = asyncio.run(instance.retrieve('TestBook Pro 2026', '学习'))
    assert not result.facts
    assert not result.verified_live


def test_mixed_primary_search_falls_back_to_original_filename_and_caches_identity():
    instance = provider()
    instance.kb_id = 'kb1'
    instance.asset_locators = lambda product: {}
    calls, media_calls = [], []
    async def call(endpoint, body):
        assert endpoint == 'search_knowledge'
        calls.append(body['query'])
        if body['query'] == '产品事实.md':
            return {'info_list':[{'media_id':'wrong','title':'产品事实.md'}, {'media_id':'correct','title':'产品事实.md'}]}
        return {'info_list':[{'media_id':'discovery','title':'TestBook Pro 2026.md'}] + [{'media_id':f'unrelated{i}','title':f'竞品营销{i}.md'} for i in range(74)]}
    async def directory(*args, **kwargs):
        raise AssertionError('Successful filename fallback must not consume exhausted getlist quota')
    async def media(row):
        media_calls.append(row['media_id'])
        if row['media_id'] == 'discovery':
            text = '# TestBook Pro 2026\n## 官网公开定位摘要\n内存：999GB\n## 建库状态\n等待完整原文\n'
        elif row['media_id'] == 'wrong':
            text = '# OtherBook 2025 - 产品事实\n## 内存\n32GB\n## 操作系统\nWindows 11\n'
        else:
            assert row['media_id'] == 'correct'
            text = '# TestBook Pro 2026 - 产品事实\n## 内存\n16GB\n## 操作系统\nHarmonyOS\n'
        return text.encode(), 'text/markdown'
    instance.call, instance.index, instance.media = call, directory, media
    async def twice():
        first = await instance.retrieve('TestBook Pro 2026', '学习')
        second = await instance.retrieve('TestBook Pro 2026', '会议')
        return first, second
    first, second = asyncio.run(twice())
    assert first.facts and second.facts
    assert all(f.product == 'TestBook Pro 2026' for f in first.facts)
    assert all('999' not in f.value and '32GB' not in f.value and 'Windows' not in f.value for f in first.facts)
    assert calls.count('产品事实.md') == 1
    assert media_calls.count('correct') == 1 and media_calls.count('wrong') == 1


@pytest.mark.parametrize('image_bytes,expected_verified', [(b'\x89PNG\r\n\x1a\nsynthetic', True), (b'<html>not a PNG</html>', False)])
def test_prefix_images_require_exact_local_filename_and_remote_magic(image_bytes, expected_verified):
    instance = provider()
    instance.kb_id = 'kb1'
    filename = 'hwpc_testbook_001.png'
    instance.asset_locators = lambda product: {filename:{'prefix':'hwpc_testbook','product':product,'title':'合成精确型号图片'}}
    media_calls = []
    async def call(endpoint, body):
        if body['query'] == 'hwpc_testbook':
            return {'info_list':[{'media_id':'good-img','title':filename,'media_type':9}, {'media_id':'wrong-img','title':'hwpc_testbook_other.png','media_type':9}]}
        return {'info_list':[{'media_id':'doc','title':'TestBook Pro 2026.md'}]}
    async def media(row):
        media_calls.append(row['media_id'])
        if row['media_id'] == 'doc':
            return b'# TestBook Pro 2026 - \xe4\xba\xa7\xe5\x93\x81\xe4\xba\x8b\xe5\xae\x9e\n## \xe5\x86\x85\xe5\xad\x98\n16GB\n', 'text/markdown'
        assert row['media_id'] == 'good-img'
        return image_bytes, 'image/png'
    instance.call, instance.media = call, media
    result = asyncio.run(instance.retrieve('TestBook Pro 2026', '学习'))
    assert 'wrong-img' not in media_calls
    assert bool(result.assets) == expected_verified
    assert result.verified_live  # Exact remote facts remain valid even when the image is rejected.


@pytest.mark.parametrize('message,reason', [('资料获取次数已达上限，请明天再尝试','quota_exhausted'), ('没有权限 CANARY_SECRET','business')])
def test_quota_classification_requires_explicit_upstream_message(monkeypatch, message, reason):
    install_transport(monkeypatch, lambda request: httpx.Response(200,json={'code':220021,'msg':message}))
    with pytest.raises(ImaError) as error:
        asyncio.run(provider().call('get_media_info', {'media_id':'image'}))
    assert error.value.code == 220021
    assert error.value.reason == reason
    assert 'CANARY_SECRET' not in str(error.value)


def test_first_image_quota_exhaustion_stops_remaining_images_but_preserves_facts():
    instance = provider()
    instance.kb_id = 'kb1'
    filenames = [f'hwpc_testbook_{i}.png' for i in range(3)]
    instance.asset_locators = lambda product: {name:{'prefix':'hwpc_testbook','product':product,'title':name} for name in filenames}
    images_requested = []
    async def call(endpoint, body):
        if body['query'] == 'hwpc_testbook':
            return {'info_list':[{'media_id':f'img{i}','title':name,'media_type':9} for i,name in enumerate(filenames)]}
        return {'info_list':[{'media_id':'doc','title':'TestBook Pro 2026.md'}]}
    async def media(row):
        if row['media_id'] == 'doc':
            return '# TestBook Pro 2026 - 产品事实\n## 内存\n16GB\n'.encode(), 'text/markdown'
        images_requested.append(row['media_id'])
        raise ImaError('ima该接口资料获取次数已达上限，请明天再尝试',220021,'quota_exhausted')
    instance.call, instance.media = call, media
    result = asyncio.run(instance.retrieve('TestBook Pro 2026', '学习'))
    assert len(images_requested) == 1
    assert result.facts and result.verified_live
    assert not result.assets
    assert any('上限' in warning for warning in result.warnings)
    from app.models import TaskInput, AuditItem
    from app.workflow import AgentWorkflow
    class EvidenceProvider:
        async def retrieve(self, product, query): return result
    class PositiveModel:
        enabled = True
        async def generate(self, task, bundle, strategy):
            fact = bundle.facts[0]
            return {'title':'任务建议','blocks':[{'text':fact.value,'fact_ids':[fact.id]}],'images':[]}
        async def verify(self, draft, bundle):
            return [AuditItem(severity='info',category='semantic',message='所有事实均有直接支持，核验通过')]
    task = TaskInput(product='TestBook Pro 2026',audience='学生',scene='学习',mode='live',platform='moments')
    final = asyncio.run(AgentWorkflow(EvidenceProvider(),PositiveModel()).run(task))
    assert final.status == 'blocked'
    assert any(item.category == 'image_count' and item.severity == 'error' for item in final.audit)


def test_trusted_ram_seed_preserves_origin_and_avoids_media_api():
    instance = provider()
    instance.kb_id = 'kb1'
    instance.asset_locators = lambda product: {}
    text = '# TestBook Pro 2026 - 产品事实\n## 内存\n16GB\n'
    seed = instance.seed_documents([({'media_id':'prior-response','title':'产品事实.md','media_type':7},text)])
    assert seed['accepted'] == 1 and seed['storage'] == 'process_memory_only'
    async def call(endpoint, body):
        assert endpoint == 'search_knowledge'
        return {'info_list':[]}
    async def forbidden_media(row):
        raise AssertionError('Trusted prior original must remain in RAM without another media fetch')
    instance.call, instance.media = call, forbidden_media
    result = asyncio.run(instance.retrieve('TestBook Pro 2026','学习'))
    assert result.facts and result.verified_live
    assert any('缓存' in warning for warning in result.warnings)
    assert any('缓存' in source.title for source in result.sources)


def test_trusted_ram_seed_rejects_sha_mismatch_and_discovery_card():
    instance = provider()
    row = {'media_id':'prior-response','title':'产品事实.md','media_type':7}
    result = instance.seed_documents([
        {'row':row,'text':'# TestBook Pro 2026 - 产品事实\n## 内存\n16GB\n','sha256':'0'*64},
        (row,'# TestBook Pro 2026\n## 官网公开定位摘要\n内存：16GB\n'),
    ])
    assert result['accepted'] == 0
    assert not instance._seeded_documents
