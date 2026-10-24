"""Exact ordinary-reading inputs and blinded actual-artifact delivery."""
import asyncio
from copy import deepcopy
import hashlib
from io import BytesIO
import json

from PIL import Image
import pytest

from app.voice_quality import build_voice_review_context
from app.marketing_review import review_marketing
from scripts.build_living_content_review import build_review
from test_marketing_review import review


def test_first_glance_uses_actual_primary_copy_not_private_strategy_or_alternatives():
    draft = {'title': '电脑是真沉', 'intro': '', 'blocks': [{'text': '圆键盘含电池约1.33kg。',
              'disclosures': ['测量存在差异']}], 'strategy': {'explanation': '作者说这是有分量'},
             'creative_plan': {'test_variants': [{'title': '另一标题'}]}}
    before = deepcopy(draft)
    result = build_voice_review_context({'platform': 'moments'}, draft)
    assert result['first_glance'] == {'title': '电脑是真沉', 'body:0': '圆键盘含电池约1.33kg。'}
    assert result['headline_first'] == {'title': '电脑是真沉'}
    assert '作者说' not in json.dumps(result, ensure_ascii=False)
    assert '另一标题' not in json.dumps(result, ensure_ascii=False)
    assert result['relationship'] == 'mixed_or_unknown'
    assert draft == before


def test_voice_identity_uses_bounded_editorial_context_not_task_role_claim():
    result = build_voice_review_context({'platform': 'wechat', 'publisher_identity': 'customer'},
        {'title': '我的意见'}, context={'publisher': {'kind': 'regional_agent', 'relationship': 'unknown'},
                                     'customer': {'purchase_stage': 'ready'}})
    assert result['publisher_identity'] == 'regional_agent'
    assert 'purchase_stage' not in result
    assert 'authority' in result['speaker_experience_authority'] or 'authorize' in result['speaker_experience_authority']
    assert build_voice_review_context({}, {}, context={'publisher': {'kind': 'admin'}})['publisher_identity'] == 'store'


def test_ordinary_reading_does_not_add_calls_or_force_positive_decision():
    class Client:
        def __init__(self): self.calls = 0
        async def _json(self, system, payload, temperature):
            self.calls += 1; self.system = system; self.payload = payload
            return review()
    client = Client()
    result = asyncio.run(review_marketing(client, task={'platform': 'moments'},
        draft={'title': '先别急着换手机', 'blocks': [{'text': '手里的设备还能满足你，也可以先不买。'}]},
        evidence={'facts': []}))
    assert client.calls == 1 and result['decision'] == 'accept'
    assert client.payload['voice_review_context']['first_glance']['title'] == '先别急着换手机'
    assert '不要求每帖加入' in client.system and '不输出活人感/网感分数' in client.system


def test_model_review_uses_server_execution_identity_without_overriding_explicit_context():
    class Client:
        creative_execution = {'editorial_voice': {'publisher_identity': 'regional_agent'}}
        async def _json(self, system, payload, temperature):
            self.payload = payload
            return review()
    client = Client()
    default_context = {'publisher': {'kind': 'store', 'relationship': 'mixed_or_unknown',
        'identity_rule': '从真实经营者/店员身份发声，不擅自拥有顾客的旧机、职业、收入与购买经历。'}}
    async def call(context):
        return await review_marketing(client, task={'platform': 'moments', 'publisher_identity': 'creator'},
            draft={'title': '给门店伙伴的一项观察', 'publisher_identity': 'brand'}, evidence={}, context=context)
    asyncio.run(call(default_context))
    value = client.payload['voice_review_context']
    assert value['publisher_identity'] == 'regional_agent'
    assert value['publisher_identity_source'] == 'server_creative_execution.editorial_voice'
    assert default_context['publisher']['kind'] == 'store'
    asyncio.run(call({'publisher': {'kind': 'brand', 'identity_source': 'trusted_campaign'}}))
    assert client.payload['voice_review_context']['publisher_identity'] == 'brand'
    asyncio.run(call({'publisher': {'kind': 'store'}}))
    assert client.payload['voice_review_context']['publisher_identity'] == 'store'


def payload():
    return {'cases': [{'id': 'private-comparison-source', 'task': {'platform': 'moments',
        'product': '合成设备', 'scene': '日常通勤', 'private_notes': 'not-public'}, 'variants': [
        {'label': 'secret_baseline_model', 'title': '<script>alert(1)</script>',
         'blocks': [{'text': '原样正文A'}]},
        {'label': 'secret_candidate_model', 'title': '普通的另一句', 'blocks': [{'text': '原样正文B'}]}]}]}


def test_pair_review_does_not_reveal_versions_models_private_case_ids_or_preselect_votes(tmp_path):
    value = payload()
    result = build_review(value, tmp_path/'review', asset_root=tmp_path, seed=9)
    page = (tmp_path/'review/index.html').read_text(encoding='utf-8')
    assert result['human_responses'] == 0
    assert all(text not in page for text in ['secret_baseline_model', 'secret_candidate_model',
                                            'private-comparison-source', 'not-public'])
    assert '<script>alert(1)</script>' not in page
    assert '&lt;script&gt;alert(1)&lt;/script&gt;' in page
    assert ' checked' not in page and 'selected=' not in page
    assert '两篇都不想发' in page and '信息不够' in page
    assert 'model_judgements": false' in page
    assert 'fetch(' not in page and 'localStorage' not in page
    key = json.loads((tmp_path/'review.key.json').read_text(encoding='utf-8'))
    assert key['cases'][0]['source_case_id'] == 'private-comparison-source'
    assert {v['label'] for v in key['cases'][0]['source'].values()} == {'secret_baseline_model', 'secret_candidate_model'}


def test_pair_review_embeds_verified_actual_image_bytes_and_stable_mapping(tmp_path):
    stream = BytesIO();Image.new('RGB', (40, 60), 'green').save(stream, format='PNG')
    raw = stream.getvalue();(tmp_path/'photo.png').write_bytes(raw)
    value = payload();value['cases'][0]['variants'][0]['images'] = [{'path': 'photo.png', 'caption': '现有照片'}]
    first = build_review(value, tmp_path/'one', asset_root=tmp_path, seed=17)
    second = build_review(value, tmp_path/'two', asset_root=tmp_path, seed=17)
    assert first['images_embedded'] == 1
    page = (tmp_path/'one/index.html').read_text(encoding='utf-8')
    assert 'data:image/png;base64,' in page and 'photo.png' not in page
    key = json.loads((tmp_path/'one.key.json').read_text(encoding='utf-8'))
    assert key == json.loads((tmp_path/'two.key.json').read_text(encoding='utf-8'))
    assert any(hashlib.sha256(raw).hexdigest() in entry['image_sha256'] for entry in key['cases'][0]['source'].values())


def test_pair_review_can_counterbalance_position_without_changing_content(tmp_path):
    positions = set()
    for seed in range(8):
        build_review(payload(), tmp_path/str(seed), asset_root=tmp_path, seed=seed)
        key = json.loads((tmp_path/(str(seed)+'.key.json')).read_text(encoding='utf-8'))
        positions.add(key['cases'][0]['source']['A']['input_index'])
    assert positions == {0, 1}


@pytest.mark.parametrize('path', ['../outside.png', 'https://example.com/photo.png'])
def test_pair_review_rejects_remote_or_escaped_assets(tmp_path, path):
    value = payload();value['cases'][0]['variants'][0]['images'] = [{'path': path}]
    with pytest.raises(ValueError, match='authorized asset root'):
        build_review(value, tmp_path/'review', asset_root=tmp_path)


def test_pair_review_rejects_unverified_image_and_key_inside_public_artifact(tmp_path):
    (tmp_path/'not.png').write_text('<svg onload="alert(1)"></svg>')
    value = payload();value['cases'][0]['variants'][0]['images'] = [{'path': 'not.png'}]
    with pytest.raises((ValueError, OSError)):
        build_review(value, tmp_path/'review', asset_root=tmp_path)
    with pytest.raises(ValueError, match='outside'):
        build_review(payload(), tmp_path/'review', asset_root=tmp_path, key_path=tmp_path/'review/key.json')
