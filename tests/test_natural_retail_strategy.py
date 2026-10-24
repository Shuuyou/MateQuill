"""Editorial contracts: useful narrative entries survive connector assembly."""
import json
from pathlib import Path

import pytest

from app.creative_connectors import get_connector
from app.models import TaskInput, EvidenceBundle


DATA = json.loads((Path(__file__).parents[1] / 'app/creative_connectors/strategies.v1.json').read_text(encoding='utf-8'))


@pytest.mark.parametrize('platform', ['xiaohongshu', 'wechat', 'moments', 'poster'])
def test_connector_exposes_three_usable_grounded_story_entries(platform):
    brief = get_connector(platform).build_brief(TaskInput(platform=platform), EvidenceBundle(product='HUAWEI MateBook'), {})
    profile = brief['playbook']
    entries = profile['narrative_entrypoints']
    assert {entry['id'] for entry in entries} == {'real_material', 'public_event', 'practical_suggestion'}
    for entry in entries:
        assert all(entry[field] for field in ('when', 'opening_method', 'example_input', 'example_opening', 'visual_method'))
        assert len(entry['example_opening']) > 15
    # A factual qualification is available without becoming the narrative spine.
    assert '贴' in profile['condition_placement'] and '证据字段' in profile['condition_placement']
    assert '经历记录' in profile['experience_basis']
    assert '核对清单' not in profile['structure'] and '适用边界' not in profile['structure']


@pytest.mark.parametrize('book_id', ['xhs_course_list', 'xhs_final_file', 'xhs_travel_cable', 'xhs_library_flow'])
def test_existing_styles_keep_story_progression_without_compliance_finale(book_id):
    task = TaskInput(platform='xiaohongshu', creative_style=book_id)
    brief = get_connector('xiaohongshu').build_brief(task, EvidenceBundle(product='HUAWEI MateBook'), {})
    book = brief['creative_playbook']
    assert book['id'] == book_id and len(book['cards']) == 5
    assert book['cards'][-1]['role'] == 'closing'
    visible_copy = str(book['opening']) + str([card['headline'] for card in book['cards']])
    assert '核对清单' not in visible_copy and '哪些有依据' not in visible_copy
    assert all(card['visual'] for card in book['cards'])
    assert book['evidence_requirements']  # Necessary fact constraints stay available.


def test_no_unsourced_first_person_retail_experience_in_new_examples():
    for profile in DATA['platform_profiles'].values():
        for entry in profile['narrative_entrypoints']:
            opening = entry['example_opening']
            assert '我在门店' not in opening and '常被问' not in opening
            assert '还有一条边界' not in opening and '不得' not in opening
    for book in DATA['playbooks']:
        assert '还有一条边界' not in json.dumps(book, ensure_ascii=False)
