from pathlib import Path

import pytest

from scripts.deploy_lab import destination_within_home, source_files


@pytest.mark.parametrize("target", ["/", "/home/test", "/etc/app", "../other", "/home/test/app/../../escape"])
def test_deploy_refuses_paths_outside_named_home_subdirectory(target):
    with pytest.raises(ValueError):
        destination_within_home("/home/test", target)


def test_deploy_resolves_relative_target_under_home():
    assert destination_within_home("/home/test", "huawei") == "/home/test/huawei"


def test_deploy_upload_excludes_secrets_and_research(tmp_path):
    for name in ["app/main.py", "app/.env", "app/secret.key", "app/__pycache__/cache.pyc", "research/private.txt", "web/index.html", "requirements.txt"]:
        path = tmp_path / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("test")
    names = {path.relative_to(tmp_path).as_posix() for path in source_files(tmp_path)}
    assert names == {"app/main.py", "web/index.html", "requirements.txt"}
