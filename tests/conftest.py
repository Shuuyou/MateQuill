"""Legacy workflow fixtures are explicit test identities, never a product flag."""
import pytest


@pytest.fixture(autouse=True)
def legacy_workflow_identity(request, monkeypatch, tmp_path):
    if request.node.get_closest_marker('real_tenancy'):
        yield
        return
    from app.tenancy import TenantContext, set_context, reset_context
    from app.tenancy import auth
    import app.run_records as run_records
    monkeypatch.setenv('MATEQUILL_DATABASE_URL', 'sqlite:///' + (tmp_path / 'test-tenancy.sqlite3').as_posix())
    monkeypatch.setenv('MATEQUILL_STORE_DB', str(tmp_path / 'test-stores.sqlite3'))
    monkeypatch.setattr(run_records, 'ROOT', tmp_path)
    identity = TenantContext(user_id='legacy-test-user', tenant_id='legacy-test-tenant',
        roles=('admin',), all_stores=True, is_legacy=True)
    token = set_context(identity)
    monkeypatch.setattr(auth, 'resolve_request_context', lambda request: identity)
    try:
        yield
    finally:
        reset_context(token)
