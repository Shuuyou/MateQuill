"""Expressive retail copy is distinct from unsupported product promises."""
import pytest

from test_creative_integration import card, run_with_card


@pytest.mark.parametrize('headline', [
    '这份下班快乐，顶级',
    '最强的仪式感，是给自己留点时间',
    '包里东西够多了，耳机别再抢戏',
    '今天的好心情，遥遥领先',
])
def test_emotion_and_metaphor_do_not_trigger_a_blanket_word_ban(headline):
    issues = run_with_card(card(headline=headline))
    assert not any(x.category == 'compliance' for x in issues)


@pytest.mark.parametrize('headline', [
    '保证兼容所有专业软件',
    '绝对不卡',
    '全网最低',
    '亲测效率翻倍',
])
def test_expressive_policy_keeps_specific_unsupported_promises_blocked(headline):
    issues = run_with_card(card(headline=headline))
    assert any(x.category == 'compliance' and x.severity == 'error' for x in issues)


def test_long_qualification_is_not_repeated_in_copy_when_adjacent_footnote_keeps_it():
    from app.editorial import deduplicate_public_footnotes
    from app.models import RunResult
    from test_workflow_adversarial import evidence
    bundle = evidence()
    condition = '受产品配置和制造工艺影响，实际重量以实物为准。'
    bundle.facts[0].qualifiers = [condition]
    result = RunResult(blocks=[{'text': '让随身物件轻一点。' + condition,
                               'fact_ids': ['f1'], 'disclosures': [condition]}],
        creative_plan={'cards': [{'body': '包里的小物件。' + condition,
                                  'fact_ids': ['f1'], 'disclosures': [condition]}]})
    assert deduplicate_public_footnotes(result, bundle) == 2
    assert result.blocks[0].text == '让随身物件轻一点。'
    assert result.creative_plan['cards'][0]['body'] == '包里的小物件。'
    assert result.blocks[0].disclosures == [condition]
    assert result.creative_plan['cards'][0]['disclosures'] == [condition]


def test_no_qualification_is_removed_without_its_matching_supported_public_footnote():
    from app.editorial import deduplicate_public_footnotes
    from app.models import RunResult
    from test_workflow_adversarial import evidence
    condition = '受产品配置和制造工艺影响，实际重量以实物为准。'
    bundle = evidence()
    bundle.facts[0].qualifiers = [condition]
    result = RunResult(blocks=[{'text': '这是正文。' + condition, 'fact_ids': ['f1']}])
    assert deduplicate_public_footnotes(result, bundle) == 0
    assert condition in result.blocks[0].text
    result.blocks[0].disclosures = [condition]
    bundle.facts[0].qualifiers = []
    assert deduplicate_public_footnotes(result, bundle) == 0
    assert condition in result.blocks[0].text
