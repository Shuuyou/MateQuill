from fastapi.testclient import TestClient

from app import api


def client():
    return TestClient(api.app)


def test_status_never_serializes_provider_credentials(monkeypatch):
    monkeypatch.setattr(api.settings, "ima_api_key", "CANARY_IMA_SECRET")
    monkeypatch.setattr(api.settings, "llm_api_key", "CANARY_LLM_SECRET")
    response = client().get("/api/status")
    assert response.status_code == 200
    assert "CANARY" not in response.text
    assert response.json()["settings"]["ima_configured"] is False or isinstance(response.json()["settings"]["ima_configured"], bool)


def test_nonloopback_settings_update_is_forbidden(monkeypatch):
    before = api.settings.ima_api_key
    response = client().post("/api/settings", json={"ima_api_key": "CANARY_SECRET"})
    assert response.status_code == 403
    assert api.settings.ima_api_key == before


def test_loopback_rejects_cross_origin_settings():
    with TestClient(api.app, client=("127.0.0.1", 12345), base_url="http://localhost:8000") as local:
        response = local.post("/api/settings", headers={"origin": "https://evil.invalid"}, json={"ima_api_key": "CANARY_SECRET"})
    assert response.status_code == 403


def test_loopback_rejects_dns_host_header_settings():
    with TestClient(api.app, client=("127.0.0.1", 12345), base_url="http://evil.invalid") as local:
        response = local.post("/api/settings", json={"ima_api_key": "CANARY_SECRET"})
    assert response.status_code == 403


def test_draft_markdown_and_html_exports_have_watermark_and_escape_script():
    api.runs["synthetic-draft"] = {"run_id": "synthetic-draft", "mode": "demo", "status": "draft", "title": "<script>alert(1)</script>", "blocks": [{"text": "合成正文", "fact_ids": ["f1"]}]}
    try:
        markdown = client().get("/api/runs/synthetic-draft/export?format=md")
        assert markdown.status_code == 200
        assert "草稿" in markdown.text and "合成正文" in markdown.text
        html = client().get("/api/runs/synthetic-draft/export?format=html")
        assert "&lt;script&gt;" in html.text
        assert "<script>" not in html.text
        assert "草稿" in html.text
    finally:
        api.runs.pop("synthetic-draft", None)


def test_blocked_export_refused():
    api.runs["synthetic-blocked"] = {"status": "blocked", "title": "不得发布"}
    try:
        for format in ("md", "html", "json"):
            assert client().get(f"/api/runs/synthetic-blocked/export?format={format}").status_code == 409
    finally:
        api.runs.pop("synthetic-blocked", None)


def test_unknown_format_and_unknown_run_are_rejected():
    assert client().get("/api/runs/no-such-run/export").status_code == 404
    api.runs["synthetic"] = {"status": "draft"}
    try:
        assert client().get("/api/runs/synthetic/export?format=exe").status_code == 422
    finally:
        api.runs.pop("synthetic", None)
