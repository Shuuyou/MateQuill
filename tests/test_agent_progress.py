from app.gtm_agent.progress import project_progress, project_process


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


def test_process_folds_completed_tools_and_active_tool():
    body = {'observations': [
                {'tool': 'research_store', 'result': 'ok', 'started_at': 100.0, 'ts': 103.5},
                {'tool': 'decision_validation', 'error_code': 'invalid_model_json'},
                {'tool': 'generate_content', 'result': 'ok', 'started_at': 104.0, 'ts': 130.0}],
            'current_tool': 'create_design_bundle', 'tool_started_at': 131.0,
            'created_at': 100.0}
    process = project_process(body, 'running')
    steps = process['steps']
    assert [step['key'] for step in steps] == ['research_store', 'generate_content', 'create_design_bundle']
    assert steps[-1]['status'] == 'active' and all(step['status'] == 'done' for step in steps[:-1])
    assert process['elapsed_ms'] > 0
    assert steps[0]['duration_ms'] == 3500 and steps[1]['duration_ms'] == 26000
    assert steps[-1]['elapsed_ms'] >= 0


def test_process_marks_failed_tool_and_hides_internal_checks():
    body = {'observations': [{'tool': 'create_poster', 'error': 'boom'}],
            'current_tool': None, 'created_at': 50.0, 'updated_at': 60.0}
    process = project_process(body, 'failed')
    assert process['steps'] == [{'key': 'create_poster', 'title': '制作海报', 'status': 'failed'}]
    assert process['elapsed_ms'] == 10000


def test_process_attaches_event_details_to_active_step():
    body = {'observations': [], 'current_tool': 'generate_content', 'created_at': 10.0,
            'events': [{'seq': 1, 'stage': 'retrieval', 'detail': '依据 6 · 素材 4'},
                       {'seq': 2, 'stage': 'drafting'},
                       {'seq': 3, 'stage': 'drafting', 'detail': '草稿第 1 版'}]}
    process = project_process(body, 'running')
    active = process['steps'][-1]
    assert active['status'] == 'active'
    assert [item['stage'] for item in active['details']] == ['检索产品资料', '起草内容']


def test_queued_turn_shows_a_planning_step():
    assert project_process({}, 'queued')['steps'] == [
        {'key': 'planning', 'title': '整理资料，确定创作方向', 'status': 'active'}]
    assert project_process({}, 'completed') is None
