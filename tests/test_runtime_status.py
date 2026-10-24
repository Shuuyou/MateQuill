import asyncio
import pytest
from app import api
from test_gtm_agent_core import identities, acting

pytestmark = pytest.mark.real_tenancy


def test_runtime_status_proves_loaded_roles_without_credentials(identities, monkeypatch):
    monkeypatch.setenv('MATEQUILL_BUILD_REVISION', 'test-revision')
    monkeypatch.setattr(api.settings, 'fact_review_use_planner', True)
    monkeypatch.setattr(api.settings, 'marketing_review_use_planner', True)
    monkeypatch.setattr(api.settings, 'llm_api_key', 'PRIVATE_CANARY_KEY')
    with acting(identities[0]):
        value = asyncio.run(api.runtime_status())
    assert value['review_roles'] == {'fact': True, 'marketing': True}
    assert value['build_revision'] == 'test-revision'
    assert value['models']['fact_review'] == api.settings.llm_model
    assert 'PRIVATE_CANARY_KEY' not in str(value)
