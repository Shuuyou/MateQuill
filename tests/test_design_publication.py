"""Image strategy changes with the actual publication package, not just ratio."""
import asyncio
from app.design.publication import publication_strategy
from app.design.planner import plan_design, source_texts
from app.design_service import _visual_marketing_context
from test_design_copy import ScriptedLLM, run, card, audit


SOURCE = {'title': '看看这抹绿', 'blocks': [{'text': '外观之外，也看看日常使用的取舍。'}]}


def test_native_title_only_excludes_absent_body_without_mutating_source():
    strategy = publication_strategy('小红书', instruction='这次只要图和标题，不写正文', source=SOURCE)
    assert strategy.mode == 'xhs_title_only'
    assert strategy.companion_copy == {'title': SOURCE['title']}
    assert SOURCE['blocks'][0]['text']
    with_body = publication_strategy('小红书', source=SOURCE)
    assert with_body.mode == 'xhs_title_body'
    assert with_body.companion_copy['body'] == SOURCE['blocks'][0]['text']
    assert publication_strategy('小红书', source={'title': '一眼看懂'}).mode == 'xhs_title_only'


def test_requested_surface_overrides_existing_post_shape_and_is_carried_to_plan():
    for platform, surface, mode in [('小红书', 'poster', 'standalone_poster'),
            ('小红书', 'title_only', 'xhs_title_only'), ('小红书', 'title_body', 'xhs_title_body'),
            ('朋友圈', '', 'moments_companion')]:
        plan = asyncio.run(plan_design({'platform': platform, 'design_surface': surface}, SOURCE))
        assert plan.brief.publication.mode == mode
        if platform == '小红书':
            assert (plan.brief.platform.width, plan.brief.platform.height) == (1080, 1440)
    assert publication_strategy('小红书', instruction='做一张小红书海报', source=SOURCE).mode == 'xhs_title_body'


def test_writer_and_final_reviewer_share_requested_package():
    llm = ScriptedLLM({'cards': [card()]}, audit('cover-1'))
    result = run(llm, task={'platform': '小红书', 'design_surface': 'title_only'}, record=SOURCE)
    assert result['status'] == 'approved'
    assert llm.calls[0]['payload']['context']['publication']['mode'] == 'xhs_title_only'
    review = _visual_marketing_context(SOURCE, {'platform': '小红书', 'design_surface': 'title_only'}, {}, {})
    assert review['publication']['mode'] == 'xhs_title_only'
    assert 'blocks' not in review['published_copy'] and 'body' not in review['published_copy']
    assert review['published_copy']['title'] == SOURCE['title']


def test_product_support_lines_need_their_own_complete_clause_review():
    proposed = card(supporting_lines=['测试数据20小时（实验室条件）'], fact_ids=['fact-battery'])
    missing = run(ScriptedLLM({'cards': [proposed]}, audit('cover-1')))
    assert missing['status'] == 'rejected'
    checked = audit('cover-1')
    checked['checks'][0]['clauses'] = [{'quote': proposed['supporting_lines'][0],
        'kind': 'product_claim', 'evidence_ids': ['fact-battery'], 'verdict': 'supported'}]
    approved = run(ScriptedLLM({'cards': [proposed]}, checked))
    assert approved['status'] == 'approved'
    catalog = source_texts({'_design_copy': approved})
    line = catalog['design-copy-0-support-0']
    assert line['text'] == proposed['supporting_lines'][0]
    assert line['fact_ids'] == ['fact-battery']
    checked['checks'][0]['clauses'][0]['quote'] = '测试数据20小时'
    incomplete = run(ScriptedLLM({'cards': [proposed]}, checked))
    assert incomplete['status'] == 'rejected'


def test_supporting_numeric_claim_cannot_hide_from_existing_evidence_gate():
    llm = ScriptedLLM({'cards': [card(supporting_lines=['续航20小时'])]})
    result = run(llm)
    assert result['status'] == 'unavailable' and len(llm.calls) == 1
