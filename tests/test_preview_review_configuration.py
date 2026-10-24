from scripts.start_final_upgrade_preview import preview_environment


def settings():
    return {'LLM_BASE_URL': 'https://example.com', 'LLM_API_KEY': 'SYNTHETIC', 'LLM_MODEL': 'test',
            'CONTENT_MODEL': 'content-test'}


def test_preview_restart_preserves_validated_review_roles():
    env = preview_environment(settings(), {})
    assert env['MATEQUILL_FACT_REVIEW_USE_PLANNER'] == '1'
    assert env['MATEQUILL_MARKETING_REVIEW_USE_PLANNER'] == '1'
    assert env['CONTENT_MODEL'] == 'content-test'


def test_explicit_config_and_inherited_opt_out_are_not_overridden():
    env = preview_environment({**settings(), 'MATEQUILL_FACT_REVIEW_USE_PLANNER': '0'},
                              {'MATEQUILL_MARKETING_REVIEW_USE_PLANNER': '0'})
    assert env['MATEQUILL_FACT_REVIEW_USE_PLANNER'] == '0'
    assert env['MATEQUILL_MARKETING_REVIEW_USE_PLANNER'] == '0'


def test_incomplete_connection_does_not_enable_implicit_role():
    env = preview_environment({'CONTENT_MODEL': 'writer'}, {})
    assert env['MATEQUILL_FACT_REVIEW_USE_PLANNER'] == '0'
    assert env['MATEQUILL_MARKETING_REVIEW_USE_PLANNER'] == '0'
