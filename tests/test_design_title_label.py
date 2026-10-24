from app.design_service import _delivered_title


def title(text):
    return _delivered_title({'title': '旧标题'}, [{'document': {'layers': [
        {'type': 'text', 'role': 'headline', 'visible': True, 'text': text}]}}])


def test_canvas_line_breaks_do_not_split_chinese_word_in_artifact_label():
    assert title('下课后，看看心\n率，也问问感受') == '下课后，看看心率，也问问感受'


def test_authored_latin_lines_keep_a_word_boundary_in_label():
    assert title('One more\nthing') == 'One more thing'
