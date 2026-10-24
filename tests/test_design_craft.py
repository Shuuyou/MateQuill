from copy import deepcopy

import pytest

from app.design.craft import build_craft_contract, build_craft_revision, check_craft_contract, check_craft_revision


def brief(platform='小红书', surface='note_cover'):
    return {'platform': {'platform': platform, 'surface': surface, 'width': 1080, 'height': 1440,
                         'image_sequence': ['cover', 'detail', 'invitation']},
            'product': 'Device A', 'asset_ids': ['rear'],
            'texts': {'title': {'text': 'A single approved message', 'fact_ids': ['f1']}},
            'publisher_kind': 'store'}


def asset(**changes):
    return {'product': 'Device A', 'sha256': 'a'*64, 'source_id': 's1',
            'visual_role': 'product_packshot', 'semantic_basis': 'pixel_review_sha256',
            'visible_views_basis': 'pixel_review_sha256', 'visible_views': ['rear'],
            'width': 1600, 'height': 1600, **changes}


def plan():
    return {'brief': brief(), 'selected_concept_id': 'c1', 'concepts': [{
        'id': 'c1', 'composition': [
            {'id': 'hero', 'role': 'image', 'image_kind': 'product', 'asset_id': 'rear',
             'x': .1, 'y': .25, 'width': .8, 'height': .6},
            {'id': 'title', 'role': 'headline', 'text_id': 'title', 'x': .1, 'y': .1,
             'width': .8, 'height': .1, 'letter_spacing': 0, 'line_height': 1.15}]}]}


def test_contract_preserves_single_source_message_and_separates_model_intention():
    b = brief()
    c = build_craft_contract(b, {}, {'rear': asset()})
    assert c['communication']['single_proposition'] == b['texts']['title']['text']
    assert c['source_selection']['hero_asset_ids'] == ['rear']
    assert c['source_selection']['recommended_method'] == 'native_assembly'
    assert 'light_material' in c['model_intentions']
    assert 'physical relighting' in c['render_controls']['scope']
    assert '3d_viewpoint' not in c['render_controls']['region_fields']
    assert not check_craft_contract(c, plan())['structural_issues']


def test_missing_source_does_not_invent_copy_or_require_screenshot_upscale():
    b = brief(); b['texts'] = {}
    c = build_craft_contract(b, {}, {'rear': asset(visual_role='official_detail', width=300, height=1800)})
    assert c['communication']['needs_art_direction']
    assert c['communication']['single_proposition'] == ''
    assert c['source_selection']['recommended_method'] == 'editorial_information'
    assert not c['source_selection']['hero_asset_ids']
    codes = {x['code'] for x in check_craft_contract(c, plan())['structural_issues']}
    assert 'unsuitable_product_hero' in codes


def test_angles_require_inspected_view_not_title_or_first_available_subject():
    c = build_craft_contract(brief(), {'craft_requirements': {'required_view': 'front'}}, {'rear': asset(title='front')})
    assert c['source_selection']['missing_view']
    assert not c['source_selection']['hero_asset_ids']
    assert any(x['code'] == 'missing_source_view' for x in check_craft_contract(c, plan())['structural_issues'])
    # Unreviewed tags also cannot certify a requested view.
    c = build_craft_contract(brief(), {'craft_requirements': {'required_view': 'rear'}},
                             {'rear': asset(visible_views_basis='filename')})
    assert c['source_selection']['missing_view']


def test_platform_and_task_change_reading_job_without_brand_templates():
    a = build_craft_contract(brief('朋友圈'), {}, {})
    b = build_craft_contract(brief('微信公众号', 'article_body'), {}, {})
    assert a['reading']['sequence'] == ['one_observation_or_invitation']
    assert b['reading']['sequence'] == ['one_explanation_with_evidence']
    assert a['id'] != b['id']


def test_product_mismatch_is_not_borrowed_and_small_source_localized():
    c = build_craft_contract(brief(), {}, {'rear': asset(product='Device B')})
    assert c['source_selection']['assets'] == {}
    c = build_craft_contract(brief(), {}, {'rear': asset(width=200, height=200)})
    issues = check_craft_contract(c, plan())['structural_issues']
    assert any(x['code'] == 'source_upscale_over_2x' and x['region_id'] == 'hero' for x in issues)


def test_contain_resolution_uses_fitted_product_not_long_empty_box():
    p = plan(); p['concepts'][0]['composition'][0].update(width=.1, height=.9)
    c = build_craft_contract(brief(), {}, {'rear': asset(width=200, height=200)})
    assert 'source_upscale_over_2x' not in {x['code'] for x in check_craft_contract(c, p)['structural_issues']}


def test_source_integrity_and_unknown_text_checks_do_not_pretend_pixel_or_fact_acceptance():
    c = build_craft_contract(brief(), {}, {'rear': asset()})
    p = plan(); p['concepts'][0]['composition'][1]['text_id'] = 'invented'
    review = check_craft_contract(c, p, {'assets': {'rear': asset(sha256='b'*64)}})
    assert {x['code'] for x in review['structural_issues']} == {'unknown_approved_text', 'source_binding_changed'}
    assert review['requires_actual_pixel_review'] and not review['fact_audit_replaced']
    assert review['score'] is None


def test_revision_locks_other_regions_source_copy_and_disallows_rewrite_controls():
    c = build_craft_contract(brief(), {}, {'rear': asset()}); p = plan(); before = deepcopy(p)
    order = build_craft_revision(c, p, page_id='cover', region_id='title', goal='Give the headline more space',
                                 controls=['width', 'letter_spacing'])
    assert order['locked_region_fields']['text_id'] == 'title'
    assert order['locked_other_regions'][0]['region']['asset_id'] == 'rear'
    assert order['locked_sources_and_copy']['texts'] == c['locks']['texts']
    assert p == before
    with pytest.raises(ValueError, match='unsupported_control'):
        build_craft_revision(c, p, page_id='cover', region_id='title', goal='edit', controls=['text_id'])
    with pytest.raises(ValueError, match='type_requires_text'):
        build_craft_revision(c, p, page_id='cover', region_id='hero', goal='edit', controls=['font_role'])


def test_page_scoped_revision_requires_unique_stable_target():
    p = plan(); p['concepts'][0]['pages'] = [
        {'id': 'front', 'composition': p['concepts'][0]['composition']},
        {'id': 'detail', 'composition': [dict(p['concepts'][0]['composition'][1])]}]
    c = build_craft_contract(brief(), {}, {'rear': asset()})
    order = build_craft_revision(c, p, page_id='detail', region_id='title', goal='Move only this headline', controls=['x'])
    assert order['page_id'] == 'detail'
    assert len(order['locked_other_regions']) == 2
    with pytest.raises(ValueError, match='not_unique'):
        build_craft_revision(c, p, page_id='missing', region_id='title', goal='edit', controls=['x'])


def test_nonfinite_type_values_and_input_mutation():
    b = brief(); original = deepcopy(b)
    c = build_craft_contract(b, {}, {'rear': asset()}); p = plan()
    p['concepts'][0]['composition'][1]['letter_spacing'] = float('nan')
    assert any(x['code'] == 'unsupported_type_control' for x in check_craft_contract(c, p)['structural_issues'])
    c['locks']['texts']['title']['text'] = 'changed in derived contract'
    assert b == original


def test_material_reselection_uses_existing_authorized_subject_not_screenshot():
    c = build_craft_contract(brief(), {}, {'rear': asset(visual_role='official_detail'), 'better': asset()})
    assert c['source_selection']['reselected_from_available']
    assert c['source_selection']['hero_asset_ids'] == ['better']


def test_revision_accepts_local_control_but_detects_other_concept_or_copy_edits():
    p = plan(); c = build_craft_contract(brief(), {}, {'rear': asset()})
    order = build_craft_revision(c, p, page_id='cover', region_id='title', goal='Loosen tracking', controls=['letter_spacing'])
    q = deepcopy(p); q['concepts'][0]['composition'][1]['letter_spacing'] = 2
    assert check_craft_revision(order, q)['within_scope']
    q['brief']['texts']['title']['text'] = 'A different advertisement'
    assert not check_craft_revision(order, q)['within_scope']
    q = deepcopy(p); q['concepts'].append({'id': 'unselected', 'composition': []})
    assert not check_craft_revision(order, q)['within_scope']
    q = deepcopy(p); q['concepts'][0]['composition'][0]['x'] = .2
    assert not check_craft_revision(order, q)['within_scope']


def test_contract_check_finds_approved_bank_rewrite():
    c = build_craft_contract(brief(), {}, {'rear': asset()}); p = plan()
    p['brief']['texts']['title']['text'] = 'Altered product claim'
    assert any(x['code'] == 'approved_copy_changed' for x in check_craft_contract(c, p)['structural_issues'])


def test_contact_shadow_is_image_only_control_not_claimed_relighting():
    c = build_craft_contract(brief(), {}, {'rear': asset()}); p = plan()
    order = build_craft_revision(c, p, page_id='cover', region_id='hero', goal='Ground the source subject', controls=['contact_shadow'])
    assert 'ground-contact ellipse' in order['light_control_boundary']
    with pytest.raises(ValueError, match='contact_shadow_requires_image'):
        build_craft_revision(c, p, page_id='cover', region_id='title', goal='ground', controls=['contact_shadow'])


def test_primary_rear_requirement_does_not_reject_a_legitimate_auxiliary_front_view():
    b = brief(); b['asset_ids'] = ['rear', 'front']
    c = build_craft_contract(b, {'craft_requirements': {'required_view': 'rear', 'primary_region_id': 'hero'}},
                             {'rear': asset(), 'front': asset(visible_views=['front'])})
    assert c['source_selection']['required_view_scope'] == 'primary_region'
    assert c['source_selection']['primary_region_id'] == 'hero'
    p = plan()
    p['brief'] = b
    p['concepts'][0]['composition'].append({'id': 'auxiliary', 'role': 'image', 'image_kind': 'product',
        'asset_id': 'front', 'x': .5, 'y': .4, 'width': .3, 'height': .4})
    assert not check_craft_contract(c, p)['structural_issues']
    p['concepts'][0]['composition'][0]['asset_id'] = 'front'
    issues = check_craft_contract(c, p)['structural_issues']
    assert any(x['code'] == 'missing_source_view' and x['region_id'] == 'hero' for x in issues)
    assert not any(x['code'] == 'missing_source_view' and x['region_id'] == 'auxiliary' for x in issues)


def test_missing_commissioned_primary_region_fails_even_if_another_rear_view_exists():
    c = build_craft_contract(brief(), {'craft_requirements': {'required_view': 'rear', 'primary_region_id': 'main-rear'}},
                             {'rear': asset()})
    issues = check_craft_contract(c, plan())['structural_issues']
    assert any(x['code'] == 'missing_primary_region' for x in issues)


def test_default_required_view_scope_still_applies_to_all_product_regions():
    b = brief(); b['asset_ids'] = ['rear', 'front']
    c = build_craft_contract(b, {'craft_requirements': {'required_view': 'rear'}},
                             {'rear': asset(), 'front': asset(visible_views=['front'])})
    assert c['source_selection']['required_view_scope'] == 'product_regions'
    p = plan(); p['brief'] = b
    p['concepts'][0]['composition'].append({'id': 'auxiliary', 'role': 'image', 'image_kind': 'product',
        'asset_id': 'front', 'x': .5, 'y': .4, 'width': .3, 'height': .4})
    assert any(x['code'] == 'missing_source_view' and x['region_id'] == 'auxiliary'
               for x in check_craft_contract(c, p)['structural_issues'])
