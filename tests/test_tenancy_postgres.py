"""Run against a separately migrated, non-owner PostgreSQL application role."""
import os
from uuid import uuid4

import pytest
from fastapi import HTTPException
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from app.tenancy import TenantContext, load_body, reset_context, save_body, set_context
from app.tenancy.database import db_connection, get_engine
from scripts.tenant_admin import bootstrap

pytestmark = pytest.mark.real_tenancy


@pytest.fixture
def pg_database(monkeypatch):
    target = os.getenv('MATEQUILL_TEST_POSTGRES_URL')
    if not target:
        pytest.skip('Set MATEQUILL_TEST_POSTGRES_URL to a migrated non-owner PostgreSQL database')
    monkeypatch.setenv('MATEQUILL_DATABASE_URL', target)
    suffix = uuid4().hex
    first = bootstrap(email='first-' + suffix + '@example.test', name='PG验收甲', tenant_name='隔离验收甲',
                      password='Postgres-test-only-password!')
    second = bootstrap(email='second-' + suffix + '@example.test', name='PG验收乙', tenant_name='隔离验收乙',
                       password='Postgres-test-only-password!')
    return first, second, suffix


def test_postgres_rls_cross_tenant_and_context_reset(pg_database):
    first, second, suffix = pg_database
    token = set_context(TenantContext(first['user_id'], first['tenant_id'], roles=('admin',),
                                      all_stores=True, grants=(('admin', None),)))
    try:
        save_body('run', suffix, {'secret': 'A-private-body'})
        with db_connection() as connection:
            assert connection.execute(text('SELECT count(*) FROM mq_resources WHERE id=:id'), {'id': suffix}).scalar_one() == 1
        set_context(TenantContext(second['user_id'], second['tenant_id'], roles=('admin',),
                                 all_stores=True, grants=(('admin', None),)))
        with pytest.raises(HTTPException) as error:
            load_body('run', suffix)
        assert error.value.status_code == 404
        with db_connection() as connection:
            assert connection.execute(text('SELECT count(*) FROM mq_resources WHERE id=:id'), {'id': suffix}).scalar_one() == 0
        set_context(None)
        # The pooled connection cannot retain the previous authorized tenant.
        with db_connection() as connection:
            assert connection.execute(text('SELECT count(*) FROM mq_resources')).scalar_one() == 0
    finally:
        reset_context(token)


def test_postgres_application_role_cannot_disable_rls(pg_database):
    with get_engine().begin() as connection:
        role = connection.execute(text('SELECT rolsuper,rolbypassrls FROM pg_roles WHERE rolname=current_user')).first()
        assert not role.rolsuper and not role.rolbypassrls
    with pytest.raises(DBAPIError) as error:
        with get_engine().begin() as connection:
            connection.execute(text('ALTER TABLE mq_resources DISABLE ROW LEVEL SECURITY'))
    assert error.value.orig.sqlstate == '42501'


def test_postgres_no_context_cannot_insert_private_resource(pg_database):
    first, _, suffix = pg_database
    with pytest.raises(DBAPIError) as error:
        with db_connection() as connection:
            connection.execute(text("INSERT INTO mq_resources(kind,id,tenant_id,owner_id,visibility,version,created_at,updated_at) VALUES('media',:id,:tenant,:owner,'private',1,0,0)"),
                               {'id': suffix, 'tenant': first['tenant_id'], 'owner': first['user_id']})
    assert error.value.orig.sqlstate == '42501'
