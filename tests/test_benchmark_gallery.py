"""Evidence gallery never invents delivery or silently edits independent reviews."""
import hashlib
import json
from pathlib import Path
import pytest

from PIL import Image

from scripts.render_six_dimension_benchmark import build_case, html_summary, independent_quality, main


def write(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False), encoding='utf-8')


def test_media_reference_does_not_count_as_final_delivery(tmp_path):
    case = {'case_id': 'unseen-store', 'store_name': 'A store'}
    directory = tmp_path / case['case_id']
    (directory / 'media').mkdir(parents=True)
    Image.new('RGB', (20, 30)).save(directory / 'media' / 'reference.png')
    write(directory / 'report.json', {'pipeline_completed': True, 'stages': {'image': {
        'turn_status': 'completed', 'ready': True, 'artifact': {'kind': 'design'}}}})
    result = build_case(tmp_path, case, {})
    assert result['genuinely_delivered'] is False
    assert result['reference_media_count_excluded'] == 1
    assert result['images'] == []
    assert result['independent_quality'] == 'not_reviewed'


def test_actual_render_hash_and_final_source_copy_are_exposed_without_quality_pass(tmp_path):
    case = {'case_id': 'unseen-store', 'store_name': 'A store'}
    directory = tmp_path / case['case_id']
    (directory / 'renders').mkdir(parents=True)
    path = directory / 'renders' / 'artboard-01.png'
    Image.new('RGB', (20, 30)).save(path)
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    write(directory / 'report.json', {'pipeline_completed': True, 'stages': {
        'text': {'artifact': {'id': 'initial'}}, 'image': {'turn_status': 'completed', 'ready': True,
        'artifact': {'kind': 'design'}}}, 'renders': [{'png': 'renders/artboard-01.png', 'png_sha256': digest}]})
    write(directory / 'text-run.json', {'run_id': 'initial', 'title': 'First <title>'})
    write(directory / 'image-design.json', {'source_run_id': 'rewritten'})
    write(directory / 'image-source-run.json', {'run_id': 'rewritten', 'title': 'Final <title>', 'blocks': [{'text': 'Actual body'}]})
    result = build_case(tmp_path, case, {})
    assert result['genuinely_delivered'] is True
    assert result['images'][0]['sha256'] == digest
    assert result['copy'] == 'Final <title>\n\nActual body'
    assert result['initial_copy'] == 'First <title>'
    assert result['copy_image_source_alignment'] == 'final_source_exported'
    assert result['independent_quality'] == 'not_reviewed'
    assert result['reselection']['confirmed_minimum'] == 1
    html = html_summary({'snapshot_at': 'now', 'cases': [result]})
    assert 'Final &lt;title&gt;' in html and 'Final <title>' not in html


def test_report_snapshot_preserves_reviews_and_flags_missing_reselected_copy(tmp_path):
    case = {'case_id': 'unseen-store', 'store_name': 'A store'}
    write(tmp_path / 'cases.json', [case])
    write(tmp_path / case['case_id'] / 'text-run.json', {'run_id': 'initial', 'title': 'First copy'})
    write(tmp_path / case['case_id'] / 'image-design.json', {'source_run_id': 'rewritten'})
    review = tmp_path / 'independent-text-review.json'
    write(review, {'cases': {'unseen-store': {'quality_verdict': 'needs_revision', 'notes': ['Specific finding']}}})
    original = review.read_bytes()
    assert main(['--root', str(tmp_path)]) == 0
    assert review.read_bytes() == original
    result = json.loads((tmp_path / 'summary.json').read_text(encoding='utf-8'))['cases'][0]
    assert result['copy'] == 'First copy'
    assert result['copy_image_source_alignment'] == 'final_source_not_exported'
    assert result['independent_verdicts'][0]['verdict'] == 'needs_revision'
    assert result['genuinely_delivered'] is False
def test_snapshot_retries_only_atomic_publish_on_transient_windows_lock(tmp_path, monkeypatch):
    from scripts import benchmark_store_cold_start as harness
    real_replace = harness.os.replace
    attempts = []
    def transient(source, target):
        attempts.append((source, target))
        if len(attempts) < 3:
            raise PermissionError('fixture destination reader lock')
        return real_replace(source, target)
    monkeypatch.setattr(harness.os, 'replace', transient)
    monkeypatch.setattr(harness.time, 'sleep', lambda _: None)
    harness.EvidenceWriter(tmp_path).json('report.json', {'stage': 'running'})
    assert len(attempts) == 3
    assert len({str(source) for source, _ in attempts}) == 1
    import json
    assert json.loads((tmp_path / 'report.json').read_text(encoding='utf-8')) == {'stage': 'running'}


@pytest.mark.parametrize('row,quality,inspected', [
    ({'quality_result': 'fail', 'decision': 'revise', 'status': 'evaluated'}, 'fail', True),
    ({'quality_verdict': 'needs_revision'}, 'fail', True),
    ({'quality_result': 'pass', 'decision': 'accept'}, 'pass', True),
    ({'status': 'not_evaluable'}, 'not_evaluable', False),
    ({'quality_result': 'pass', 'status': 'not_evaluable'}, 'mixed', False),
    ({'quality_result': 'fail', 'decision': 'accept'}, 'mixed', True),
    ({'quality_result': 'pass', 'quality_verdict': 'undocumented_label'}, 'mixed', True),
    ({'status': 'evaluated'}, 'unknown', True),
    ({'notes': ['record exists']}, 'unknown', False),
])
def test_explicit_independent_labels_are_preserved_without_guessing(row, quality, inspected):
    actual, checked, records = independent_quality([{'file': 'review.json', 'review': row}])
    assert (actual, checked) == (quality, inspected)
    assert records[0]['raw_verdicts'] == {key: value for key, value in row.items() if key != 'notes'}


def test_conflicting_review_files_are_mixed_never_automatically_passed():
    quality, inspected, records = independent_quality([
        {'file': 'text.json', 'review': {'quality_result': 'pass'}},
        {'file': 'image.json', 'review': {'decision': 'revise'}}])
    assert quality == 'mixed' and inspected
    assert [row['quality'] for row in records] == ['pass', 'fail']


def test_gallery_counts_actual_inspections_and_exposes_failures_without_touching_reviews(tmp_path):
    cases = [{'case_id': name, 'store_name': name} for name in ('delivered', 'missing', 'conflict', 'passed')]
    write(tmp_path / 'cases.json', cases)
    review_path = tmp_path / 'independent-image-review.json'
    document = {'cases': [
        {'case_id': 'delivered', 'quality_result': 'fail', 'status': 'evaluated', 'decision': 'revise'},
        {'case_id': 'missing', 'status': 'not_evaluable'},
        {'case_id': 'conflict', 'quality_result': 'fail', 'quality_verdict': 'pass'},
        {'case_id': 'passed', 'quality_result': 'pass'}]}
    write(review_path, document)
    original = review_path.read_bytes()
    directory = tmp_path / 'delivered'
    (directory / 'renders').mkdir(parents=True)
    image = directory / 'renders' / 'artboard-01.png'
    Image.new('RGB', (10, 10)).save(image)
    write(directory / 'report.json', {'pipeline_completed': True, 'stages': {'image': {
        'turn_status': 'completed', 'ready': True, 'artifact': {'kind': 'design'}}},
        'renders': [{'png': image.name, 'png_sha256': hashlib.sha256(image.read_bytes()).hexdigest()}]})
    assert main(['--root', str(tmp_path)]) == 0
    summary = json.loads((tmp_path / 'summary.json').read_text(encoding='utf-8'))
    assert review_path.read_bytes() == original
    totals = summary['totals']
    assert totals['genuinely_delivered'] == 1
    assert totals['with_independent_review_records'] == 4
    assert totals['independent_inspected'] == 3
    assert totals['independent_passed'] == 1
    assert totals['independent_failed'] == 1
    assert totals['independent_mixed'] == 1
    assert totals['independent_unevaluated'] == 1
    assert summary['cases'][1]['independent_quality'] == 'not_evaluable'
    html = (tmp_path / 'index.html').read_text(encoding='utf-8')
    assert 'review_available' not in html
    assert 'quality_result' in html and 'fail' in html and 'mixed' in html
