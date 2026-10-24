from app.gtm_agent.progress import project_progress


def test_progress_reports_actual_tool_without_private_inputs():
    value = project_progress({'current_tool': 'research_store', 'query': 'PRIVATE_QUERY',
                              'observations': ['PRIVATE_SOURCE']}, 'running')
    assert value == {'stage': 'research_store', 'message': '正在查门店位置和周边线索。'}
    assert 'PRIVATE' not in str(value)


def test_old_generation_turn_has_a_real_phase_fallback():
    assert project_progress({'delivery_scope': ['content']}, 'running')['stage'] == 'generate_content'
    assert project_progress({'delivery_scope': ['images'], 'artifact': {'kind': 'run'}}, 'running')['stage'] == 'create_design_bundle'


def test_terminal_turn_never_claims_it_is_still_working():
    for status in ['failed', 'completed', 'needs_input', 'cancelled']:
        assert project_progress({'current_tool': 'generate_content'}, status) is None


def test_unknown_tool_or_model_prose_is_not_exposed_as_progress():
    assert project_progress({'current_tool': 'SECRET', 'message': 'UNTRUSTED'}, 'running') == {
        'stage': 'planning', 'message': '正在整理资料，确定创作方向。'}
