import pytest

from app.marketing_surfaces import marketing_public_surfaces, marketing_surface_role, resolve_surface_finding


def draft():
    return {'title': '把晚霞装进口袋', 'intro': '', 'cta': '路过来聊聊',
        'blocks': [{'text': '手机支持最大66W有线充电。', 'disclosures': ['需搭配指定充电套装。', '实际功率随场景变化。']}],
        'images': [{'caption': '产品官方图'}],
        'creative_plan': {'cards': [{'id': 'c1', 'headline': '晚霞留在口袋里', 'body': '看看街角的颜色。',
                                    'disclosures': ['此处为创意示意。']}],
                          'test_variants': [{'title': '晚霞还没下班', 'cover_copy': '街角一瞥'}]}}


def finding(quote='', region='body:0'):
    return {'quote': quote, 'region': region, 'reason': '具体问题', 'action': '局部改写，保留晚霞双关'}


def test_exact_known_surface_fills_actual_text_without_changing_judgement():
    value = finding()
    result = resolve_surface_finding(value, marketing_public_surfaces(draft()))
    assert result['quote'] == draft()['blocks'][0]['text']
    assert result['reason'] == value['reason'] and result['action'] == value['action']
    assert value['quote'] == ''


def test_unknown_surface_id_fails_without_fuzzy_recovery():
    with pytest.raises(ValueError, match='unknown public surface'):
        resolve_surface_finding(finding(region='body:O'), marketing_public_surfaces(draft()))


def test_unknown_quote_still_rejected_even_with_valid_id():
    with pytest.raises(ValueError, match='non-public'):
        resolve_surface_finding(finding(quote='支持66W无线充电'), marketing_public_surfaces(draft()))


def test_quote_from_other_surface_is_not_accepted_for_selected_id():
    with pytest.raises(ValueError, match='non-public'):
        resolve_surface_finding(finding(quote='路过来聊聊'), marketing_public_surfaces(draft()))


def test_fields_and_roles_remain_separate():
    surfaces = marketing_public_surfaces(draft())
    assert surfaces['title'] == '把晚霞装进口袋'
    assert surfaces['variant:0.title'] == '晚霞还没下班'
    assert surfaces['card:c1.headline'] == '晚霞留在口袋里'
    assert marketing_surface_role('title') == 'primary_post'
    assert marketing_surface_role('variant:0.title') == 'candidate_alternative'
    assert marketing_surface_role('card:c1.headline') == 'storyboard'
    assert '晚霞还没下班' not in surfaces['title']


def test_all_disclosures_preserved_as_aggregate_and_individual_exact_fields():
    surfaces = marketing_public_surfaces(draft())
    assert surfaces['body:0.disclosures'] == '需搭配指定充电套装。\n实际功率随场景变化。'
    assert surfaces['body:0.disclosure:0'] == '需搭配指定充电套装。'
    assert surfaces['body:0.disclosure:1'] == '实际功率随场景变化。'
    assert surfaces['card:c1.disclosures'] == '此处为创意示意。'
    assert surfaces['card:c1.disclosure:0'] == '此处为创意示意。'
    assert surfaces['image:0.caption'] == '产品官方图'


def test_textual_empty_field_can_locate_a_specific_missing_disclosure():
    value = draft()
    value['blocks'][0]['disclosures'] = []
    resolved = resolve_surface_finding(finding(region='body:0.disclosures'), marketing_public_surfaces(value))
    assert resolved['quote'] == '' and resolved['region'] == 'body:0.disclosures'


def test_arbitrary_pixel_region_requires_actual_visual_access():
    surfaces = marketing_public_surfaces(draft())
    with pytest.raises(ValueError, match='unknown public surface'):
        resolve_surface_finding(finding(region='画面右下角人物表情'), surfaces)
    assert resolve_surface_finding(finding(region='画面右下角人物表情'), surfaces, allow_visual=True)['quote'] == ''


def test_duplicate_card_ids_cannot_silently_retarget_a_finding():
    value = draft()
    value['creative_plan']['cards'].append({'id': 'c1', 'body': '其他内容'})
    with pytest.raises(ValueError, match='Duplicate public card identity'):
        marketing_public_surfaces(value)


def test_omitted_quote_key_is_filled_not_left_missing():
    # The review schema allows a model response to omit 'quote' entirely
    # (it defaults to ''); callers then do found['quote'], so a dict key
    # must always come back, never a silent pass-through of the input.
    value = {'region': 'body:0', 'reason': '具体问题', 'action': '局部改写'}
    assert 'quote' not in value
    result = resolve_surface_finding(value, marketing_public_surfaces(draft()))
    assert result['quote'] == draft()['blocks'][0]['text']


def test_omitted_quote_on_unknown_region_still_raises_cleanly():
    with pytest.raises(ValueError, match='unknown public surface'):
        resolve_surface_finding({'region': 'body:O'}, marketing_public_surfaces(draft()))


def test_missing_region_raises_instead_of_becoming_an_unkeyed_dict():
    with pytest.raises(ValueError, match='missing its region'):
        resolve_surface_finding({'quote': ''}, marketing_public_surfaces(draft()))
