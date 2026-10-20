import asyncio

from app.intake import resolve_intake


CATALOG = [
    {'id': 'h14', 'name': 'HUAWEI MateBook 14（鸿蒙版）', 'system': '鸿蒙'},
    {'id': 'w14', 'name': 'HUAWEI MateBook 14', 'system': 'Windows'},
    {'id': 'd16', 'name': 'HUAWEI MateBook D 16', 'system': 'Windows'},
]


def resolve(query, answers=None, llm=None):
    return asyncio.run(resolve_intake(query, CATALOG, llm, answers))


def test_natural_complete_brief_needs_no_form():
    result = resolve('给设计专业大一新生写一篇MateBook14鸿蒙版的小红书，关心课程软件和手机传资料')
    assert result['status'] == 'ready'
    assert result['task']['product'] == CATALOG[0]['name']
    assert result['task']['audience'] == '设计专业大一新生'
    assert '课程软件' in result['task']['requirements']


def test_audience_after_the_request_is_not_asked_again():
    result=resolve('帮我写一篇MateBook 14的小红书，面向准备课堂展示的大学生。')
    assert result['task']['audience']=='准备课堂展示的大学生'
    assert [q['field'] for q in result['questions']]==['product']


def test_bare_family_only_offers_matching_variants():
    result = resolve('给大学生写一篇MateBook 14小红书')
    assert result['status'] == 'clarify'
    assert result['task']['product'] == ''
    assert [item['value'] for item in result['questions'][0]['options']] == [row['name'] for row in CATALOG[:2]]


def test_no_fabricated_buyer_or_product():
    result = resolve('帮我写一篇小红书')
    assert result['task']['audience'] == result['task']['product'] == ''
    assert {question['field'] for question in result['questions']} == {'product', 'audience'}


def test_empty_input_can_be_clarified_in_one_round():
    assert len(resolve('')['questions']) == 2


def test_explicit_answers_override_and_validate_catalog():
    result = resolve('帮我写一篇小红书', {'product': 'w14', 'audience': '设计专业学生', 'platform': 'moments'})
    assert result['status'] == 'ready'
    assert result['task']['product'] == CATALOG[1]['name']
    assert result['task']['platform'] == 'moments'
    assert resolve('给大学生写篇内容', {'product': '不存在的机器'})['status'] == 'clarify'


def test_brand_spacing_parentheses_and_windows_alias():
    result = resolve('给研究生写一篇华为 MateBook 14 (鸿蒙版) 的公众号')
    assert result['status'] == 'ready'
    assert result['task']['platform'] == 'wechat'
    assert resolve('给大学生写MateBook14 Windows版')['task']['product'] == CATALOG[1]['name']


def test_model_cannot_invent_audience():
    class Model:
        enabled = True

        async def _json(self, *args, **kwargs):
            return {'audience': '设计专业大学生', 'scene': '凭空想出的宿舍场景'}

    result = resolve('MateBook14鸿蒙版的小红书', llm=Model())
    assert result['status'] == 'clarify'
    assert result['task']['audience'] == ''


def test_multiple_explicit_models_are_not_silently_reduced_to_first():
    result = resolve('给大学生写MateBook14鸿蒙版或者MateBook D16的小红书')
    assert result['status'] == 'clarify'
    assert result['task']['product'] == ''
    assert len(result['questions'][0]['options']) == 3
