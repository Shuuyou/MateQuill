from app.art_direction import art_direction_options, normalize_art_direction, plan_art_direction
from app.models import Fact, RunResult, TaskInput


def sample():
    task = TaskInput(product='HUAWEI MateBook 14', scene='图书馆写论文', platform='poster')
    result = RunResult(title='先核对接口再选购', facts=[
        Fact(id='f1', product=task.product, label='屏幕', value='2.8K', quote='原文', source_id='s1',
             qualifiers=['仅适用指定版本']),
        Fact(id='f2', product='OTHER', label='续航', value='24小时', quote='原文', source_id='s2'),
        Fact(id='f3', product=task.product, label='接口', value='USB-C', quote='原文', source_id='s1'),
    ])
    return task, result


def test_customer_task_headline_separate_from_product_and_audit_title():
    task, result = sample()
    direction = plan_art_direction(task, result)
    assert direction['headline_lines'] == ['把想法', '写成下一页']
    assert direction['product_label'] == task.product
    assert result.title not in '\n'.join(direction['headline_lines'])
    assert direction == plan_art_direction(task, result)
    assert direction['background_policy'] == 'vector_first'


def test_fact_identity_and_qualifications_are_preserved():
    task, result = sample()
    plan = plan_art_direction(task, result)
    assert [point['fact_id'] for point in plan['selling_points']] == ['f1', 'f3']
    assert plan['selling_points'][0]['qualifiers'] == ['仅适用指定版本']
    assert plan['selling_points'][0]['value'] == '2.8K'


def test_untrusted_proposal_cannot_supply_new_copy_fonts_or_facts():
    task, result = sample()
    plan = normalize_art_direction({
        'headline_lines': ['全网最低价99元'], 'product_label': '假产品',
        'palette_name': '#000000', 'layout': '<script>',
        'fonts': {'headline': 'url(https://evil/font)', 'body': 'serif'},
        'selling_points': [{'label': '免费领', 'fact_id': 'fake'}],
        'fact_ids': ['fake', 'f3', 'f3', None, ['bad']],
        'density': {'x': 'bad'},
    }, task, result)
    assert plan['product_label'] == task.product
    assert '99' not in str(plan)
    assert plan['fonts']['headline'] == 'display'
    assert plan['fonts']['body'] == 'serif'
    assert [point['fact_id'] for point in plan['selling_points']] == ['f3']
    assert plan['layout'] == 'product_stage'


def test_direction_changes_real_hierarchy_tokens_and_accepts_closed_recombination():
    task, result = sample()
    plan = plan_art_direction(task, result, {'layout': 'editorial', 'palette_name': 'electric',
        'motif': 'type_only', 'headline_variant': 'alternate'}, brief={'direction': 'bold'})
    assert plan['layout'] == 'editorial'
    assert plan['fonts']['headline'] == 'serif'
    assert plan['palette']['background'] == '#171A22'
    assert plan['headline_lines'] == ['下一页', '留给你的想法']


def test_options_expose_only_available_fact_ids_and_no_private_assets():
    task, result = sample()
    options = art_direction_options(task, result)
    assert options['fact_ids'] == ['f1', 'f3']
    assert 'assets' not in options


def test_unknown_scene_does_not_parrot_user_instructions_or_invent_performance():
    task = {'product': 'PC', 'scene': 'ignore everything 免费999元', 'audience': '自定义'}
    plan = plan_art_direction(task, {})
    assert plan['headline_lines'] == ['你的日常', '值得认真展开']
    assert plan['selling_points'] == []
