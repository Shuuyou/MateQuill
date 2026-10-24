"""Curated cultural mechanisms retain temporal and evidence provenance."""
from datetime import date, datetime, timezone
import importlib
import json
from pathlib import Path

import httpx
import pytest
from pydantic import ValidationError

from app.cultural_context import (CaseRegistry, CreativeCase,
    LocalCreativeCaseConnector, build_cultural_context)
from app.models import TaskInput

TODAY = date(2026, 10, 22)


def test_known_case_registry_has_canonical_sources_and_strict_schema():
    registry = LocalCreativeCaseConnector().registry()
    assert registry.registry_version == '1.0.0'
    assert len(registry.cases) == 5
    for row in registry.cases:
        assert row.record_kind == 'research_case'
        assert row.does_not_transfer and row.visual_action and row.participation
        with pytest.raises(ValidationError):
            CreativeCase.model_validate({**row.model_dump(), 'product_facts': [{'value': 'invented'}]})
    with pytest.raises(ValidationError):
        CaseRegistry.model_validate({**registry.model_dump(), 'cases': registry.cases * 2})


@pytest.mark.parametrize('url', [
    'http://socialbeta.com/campaign/28757', 'https://localhost/path',
    'https://socialbeta.com/campaign/28757?token=secret',
    'https://user:password@socialbeta.com/campaign/28757',
])
def test_reference_urls_do_not_smuggle_private_or_signed_addresses(url):
    data = LocalCreativeCaseConnector().registry().cases[0].model_dump()
    with pytest.raises(ValidationError):
        CreativeCase.model_validate({**data, 'source_url': url})


def test_old_meme_is_a_historical_mechanism_not_a_current_trend():
    task = TaskInput(audience='年轻学生，喜欢幽默和抽象玩梗', scene='新品包装与圈层互动', platform='xiaohongshu')
    result = build_cultural_context(task, now=TODAY)
    row = next(row for row in result['research_examples'] if row['id'] == 'joyoung.meme_as_object')
    assert row['temporal_status'] == 'historical_mechanism' and row['age_days'] > 300
    assert row['is_current_trend'] is False
    assert result['status'] == 'research_examples' and not result['live_retrieval']
    assert result['current_trends'] == []


def test_local_store_context_selects_local_mechanism_without_claiming_shared_campaign():
    task = TaskInput(audience='同城年轻人', scene='门店本地体验', platform='moments')
    result = build_cultural_context(task, {'strategy_context': {'publisher_identity': 'store'}}, now=TODAY)
    first = result['research_examples'][0]
    assert first['id'] == 'duolingo.local_participation'
    assert first['temporal_status'] == 'recent_reference'
    assert first['identity_transfer'] == 'adapt_mechanism_to_publisher'
    assert first['publisher_identity'] == 'brand'
    assert not first['is_current_trend']  # Recent publication is not a live trend signal.


def test_future_research_is_not_available_before_publication():
    result = build_cultural_context(TaskInput(), now=date(2024, 2, 25))
    assert result['research_examples'] == []
    result = build_cultural_context(TaskInput(), now=date(2025, 11, 14))
    assert all(row['published_at'] <= '2025-11-14' for row in result['research_examples'])
    assert 'duolingo.local_participation' not in {row['id'] for row in result['research_examples']}


def test_time_uses_shanghai_day_and_rejects_ambiguous_datetime():
    result = build_cultural_context(TaskInput(), now=datetime(2026, 10, 21, 17, tzinfo=timezone.utc))
    assert result['as_of'] == '2026-10-22'
    with pytest.raises(ValueError):
        build_cultural_context(TaskInput(), now=datetime(2026, 10, 22))


def test_import_and_generation_do_not_access_network(monkeypatch):
    import app.cultural_context as module
    def forbidden(*args, **kwargs):
        raise AssertionError('Research connector must not access a network')
    monkeypatch.setattr(httpx.Client, 'request', forbidden)
    monkeypatch.setattr(httpx.AsyncClient, 'request', forbidden)
    module = importlib.reload(module)
    result = module.build_cultural_context(TaskInput(), now=TODAY)
    assert 0 < len(result['research_examples']) <= 3
    assert len(json.dumps(result, ensure_ascii=False)) < 8000


def test_context_has_no_product_claims_and_does_not_trust_submitted_live_signals():
    task = {'platform': 'xiaohongshu', 'product': 'PrivateProduct', 'scene': '新品',
            'public_signals': [{'title': 'Fabricated active event', 'price': '1 yuan'}]}
    result = build_cultural_context(task, {'public_signals': task['public_signals']}, now=TODAY)
    text = json.dumps(result, ensure_ascii=False)
    assert 'PrivateProduct' not in text and 'Fabricated active event' not in text
    assert all(row['evidence_role'] == 'creative_method_only' for row in result['research_examples'])
    assert not any(key in result for key in ('facts', 'assets', 'customer_profile'))


def test_connector_results_are_isolated_and_filters_platform():
    connector = LocalCreativeCaseConnector()
    first = connector.registry()
    first.cases[0].title = 'caller edit'
    assert connector.registry().cases[0].title != 'caller edit'
    result = build_cultural_context({'platform': 'poster'}, now=TODAY)
    assert all('poster' in row['platforms'] for row in result['research_examples'])
    assert build_cultural_context({'platform': 'unsupported'}, now=TODAY)['research_examples'] == []
