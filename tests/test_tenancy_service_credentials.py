import json
from pathlib import Path

import pytest

from scripts.run_credential_service import credential_environment


def write(directory, values):
    (directory / 'matequill-providers').write_text(json.dumps(values), encoding='utf8')


def test_credential_values_are_loaded_only_into_environment(tmp_path):
    write(tmp_path, {'LLM_API_KEY': 'test-only-key', 'MATEQUILL_DATABASE_URL': 'postgresql://test:fixture@localhost/test'})
    inherited = {'PORT': '7860'}
    environment = credential_environment(str(tmp_path), inherited)
    assert environment['LLM_API_KEY'] == 'test-only-key'
    assert environment['PORT'] == '7860'
    assert 'LLM_API_KEY' not in inherited


@pytest.mark.parametrize('field', ['PATH', 'PYTHONPATH', 'LD_PRELOAD', 'PORT'])
def test_provider_credential_cannot_override_executable_or_service_fields(tmp_path, field):
    write(tmp_path, {field: 'untrusted'})
    with pytest.raises(RuntimeError, match='Unexpected service credential field'):
        credential_environment(str(tmp_path), {})


def test_production_entrypoint_requires_database_and_model_credentials(tmp_path):
    write(tmp_path, {'LLM_API_KEY': 'test-only-key'})
    with pytest.raises(RuntimeError, match='Required production service credentials'):
        credential_environment(str(tmp_path), {})
