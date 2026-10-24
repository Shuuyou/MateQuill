"""Automatic editorial research exposes only bounded public query material."""
import asyncio
from copy import deepcopy
import json

from app import marketing_context as module
from app.gtm_agent.place_context import PLACE_CONTEXT_MARKER
from app.models import TaskInput


def task():
    return TaskInput(product='HUAWEI Mate 80', audience='PRIVATE_AUDIENCE_CANARY',
                     scene='PRIVATE_SCENE_CANARY', requirements='PRIVATE_REQUIREMENTS_CANARY',
                     platform='moments', store_id='PRIVATE_STORE_ID_CANARY')


def public_search_recorder():
    calls = []
    async def search(query, *, use_cache):
        calls.append((query, use_cache))
        return {'state': 'found', 'observed_at': '2026-10-23T01:00:00+08:00', 'items': [{
            'source_id': 'public-1', 'title': 'Public report', 'url': 'https://example.org/news',
            'excerpt': 'Public snippet', 'published_at': '2026-10-22',
            'private_metadata': 'DO_NOT_PROPAGATE'}]}
    return search, calls


def test_preparation_queries_only_public_product_and_extracted_city():
    search, calls = public_search_recorder()
    local = {'location_summary': '位于杭州市钱塘区 PRIVATE_STREET_CANARY',
             'private_contacts': ['PRIVATE_CONTACTS_CANARY'],
             'creative_brief': {'audience': 'PRIVATE_HYPOTHESIS_CANARY'}}
    original = deepcopy(local)
    result = asyncio.run(module.prepare_marketing_context(task(), local, search=search))
    assert len(calls) == 3
    assert all(use_cache is False for _, use_cache in calls)
    assert all('HUAWEI Mate 80' in query for query, _ in calls[:2])
    assert '官方 更新 公告' in calls[0][0]
    assert '争议 澄清 消费者' in calls[1][0]
    assert '杭州市' in calls[2][0] and '本地新闻 通报' in calls[2][0]
    assert all('PRIVATE_' not in query for query, _ in calls)
    assert local == original
    assert result['local_editorial_context'] == local
    assert result['research'][0]['sources'][0]['source_id'] == 'public-1'
    assert 'private_metadata' not in result['research'][0]['sources'][0]
    assert result['publisher']['relationship'] == 'mixed_or_unknown'


def test_unrecognized_city_uses_two_product_queries_without_location_guessing():
    search, calls = public_search_recorder()
    result = asyncio.run(module.prepare_marketing_context(task(),
        {'location_summary': 'Some anonymous mall PRIVATE_ADDRESS_CANARY'}, search=search))
    assert len(calls) == 2 and len(result['research']) == 2
    assert all('PRIVATE_' not in query for query, _ in calls)


def test_unavailable_search_remains_unknown_and_does_not_assert_absence_of_controversy():
    async def unavailable(*args, **kwargs):
        raise RuntimeError('private provider diagnostic must not escape')
    result = asyncio.run(module.prepare_marketing_context(task(), search=unavailable))
    assert len(result['research']) == 2
    assert all(item['state'] == 'unavailable' and item['sources'] == [] for item in result['research'])
    assert any('不证明没有争议' in limitation for limitation in result['evidence_limits'])
    assert 'private provider diagnostic' not in json.dumps(result, ensure_ascii=False)
    assert 'no_controversy' not in result and 'safe_to_publish' not in result


def test_enrichment_searches_at_most_two_concrete_world_time_quotes_and_removes_contacts():
    search, calls = public_search_recorder()
    context = {'private_memory': ['PRIVATE_MEMORY_CANARY'], 'research': []}
    original = deepcopy(context)
    review = {'dimensions': {
        'world': {'status': 'unverified', 'findings': [
            {'quote': '杭州市活动公告 联系13800138000 或 user@example.com'},
            {'quote': '杭州市活动公告 联系13800138000 或 user@example.com'},
            {'quote': '金鸡湖音乐喷泉每晚开放'},
            {'quote': '第三个具体事实不应发出查询'}]},
        'timeliness': {'status': 'revise', 'findings': [{'quote': '第四条具体时效主张'}]},
        'empathy': {'status': 'reselect', 'findings': [{'quote': '它的夜，比你的班亮'}]},
        'product': {'status': 'revise', 'findings': [{'quote': 'PRIVATE_PRODUCT_REVIEW_CANARY'}]}}}
    result = asyncio.run(module.enrich_review_evidence(context, review, search=search))
    assert len(calls) == 2
    joined = '\n'.join(query for query, _ in calls)
    assert '杭州市活动公告' in joined and '金鸡湖音乐喷泉' in joined
    assert '13800138000' not in joined and 'user@example.com' not in joined
    assert 'PRIVATE_' not in joined and '它的夜' not in joined and '第三个' not in joined and '第四条' not in joined
    assert all(query.endswith('来源 核实') and cache is False for query, cache in calls)
    assert context == original and len(result['targeted_research']) == 2


def test_metaphor_empathy_only_and_resolved_world_claims_do_not_trigger_external_research():
    async def forbidden(*args, **kwargs):
        raise AssertionError('metaphor or resolved finding must never become an external query')
    context = {'research': []}
    review = {'dimensions': {
        'empathy': {'status': 'reselect', 'findings': [{'quote': '把晚霞装进口袋'}]},
        'community': {'status': 'revise', 'findings': [{'quote': '表达过于企业账号化'}]},
        'world': {'status': 'pass', 'findings': [{'quote': '这是已经有充分来源的公开事实'}]},
        'timeliness': {'status': 'not_applicable', 'findings': []}}}
    result = asyncio.run(module.enrich_review_evidence(context, review, search=forbidden))
    assert result == context and 'targeted_research' not in result


def test_reselection_replaces_canonical_scene_while_preserving_sources_and_suffix():
    value = task()
    sources = [{'source_id': 'place-source-1', 'url': 'https://example.org/local', 'excerpt': 'Original public evidence'}]
    envelope = {'sources': sources, 'creative_brief': {'scene': 'old scene', 'location_summary': 'Verified public local environment',
                'audience': 'an editorial hypothesis', 'evidence_ids': ['place-source-1']}}
    suffix = '\nOther constraints must remain exactly as provided.'
    value.requirements = 'Original prefix' + PLACE_CONTEXT_MARKER + json.dumps(envelope, ensure_ascii=False) + suffix
    module.replace_rejected_scene(value, {'revision_brief': 'Choose a new local observation without defining the reader as poor'})
    raw = value.requirements.split(PLACE_CONTEXT_MARKER, 1)[1]
    updated, end = json.JSONDecoder().raw_decode(raw)
    assert updated['sources'] == sources
    assert updated['creative_brief']['evidence_ids'] == ['place-source-1']
    assert updated['creative_brief']['audience'] == envelope['creative_brief']['audience']
    assert updated['creative_brief']['scene'] == value.scene
    assert 'Verified public local environment' in value.scene
    assert updated['reselection_history'][0]['scene'] == 'old scene'
    assert raw[end:] == suffix and value.requirements.startswith('Original prefix')


def test_reselection_feedback_does_not_become_mandatory_scene_or_visual_task():
    value = task()
    envelope = {'creative_brief': {'scene': 'old scene', 'location_summary': 'Public environment'}}
    value.requirements = PLACE_CONTEXT_MARKER + json.dumps(envelope)
    feedback = 'Remove invented experience; optional idea: compare two photos, not required.'
    module.replace_rejected_scene(value, {'revision_brief': feedback})
    updated = json.loads(value.requirements.split(PLACE_CONTEXT_MARKER, 1)[1])
    assert feedback not in value.scene
    assert feedback not in updated['creative_brief']['visual_direction']
    assert updated['reselection_history'][0]['reason'] == feedback
