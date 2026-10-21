"""Transactional tenant metadata store shared by the business modules.

PostgreSQL is the production backend. SQLite exists for isolated local tests and
small offline development; application authorization runs on both backends.
"""
from contextlib import contextmanager
from contextvars import ContextVar
from functools import lru_cache
import os
from pathlib import Path

from sqlalchemy import (Boolean, Column, Float, ForeignKey, Index, Integer,
                        MetaData, String, Table, Text, create_engine, text)

metadata = MetaData()
_transaction_connection = ContextVar('matequill_tenant_transaction', default=None)
tenants = Table('mq_tenants', metadata,
    Column('id', String(64), primary_key=True), Column('name', String(160), nullable=False),
    Column('created_at', Float, nullable=False))
users = Table('mq_users', metadata,
    Column('id', String(64), primary_key=True), Column('email', String(254), nullable=False, unique=True),
    Column('name', String(120), nullable=False), Column('password_hash', Text, nullable=False),
    Column('active', Boolean, nullable=False, default=True), Column('created_at', Float, nullable=False))
memberships = Table('mq_memberships', metadata,
    Column('id', String(64), primary_key=True), Column('user_id', String(64), ForeignKey('mq_users.id'), nullable=False),
    Column('tenant_id', String(64), ForeignKey('mq_tenants.id'), nullable=False),
    Column('store_id', String(120), nullable=True), Column('role', String(40), nullable=False),
    Column('active', Boolean, nullable=False, default=True), Column('created_at', Float, nullable=False))
Index('mq_members_user_tenant', memberships.c.user_id, memberships.c.tenant_id)
sessions = Table('mq_sessions', metadata,
    Column('token_hash', String(64), primary_key=True), Column('user_id', String(64), ForeignKey('mq_users.id'), nullable=False),
    Column('tenant_id', String(64), nullable=False), Column('store_id', String(120), nullable=True),
    Column('expires_at', Float, nullable=False), Column('created_at', Float, nullable=False))
invitations = Table('mq_invitations', metadata,
    Column('id', String(64), primary_key=True), Column('token_hash', String(64), nullable=False, unique=True),
    Column('tenant_id', String(64), ForeignKey('mq_tenants.id'), nullable=False),
    Column('email', String(254), nullable=False), Column('role', String(40), nullable=False),
    Column('store_id', String(120), nullable=True), Column('created_by', String(64), nullable=False),
    Column('expires_at', Float, nullable=False), Column('used_at', Float, nullable=True))
resources = Table('mq_resources', metadata,
    Column('kind', String(48), primary_key=True), Column('id', String(160), primary_key=True),
    Column('tenant_id', String(64), ForeignKey('mq_tenants.id'), nullable=False),
    Column('store_id', String(120), nullable=True), Column('owner_id', String(64), nullable=False),
    Column('visibility', String(20), nullable=False, default='private'), Column('body', Text, nullable=True),
    Column('version', Integer, nullable=False, default=1), Column('created_at', Float, nullable=False),
    Column('updated_at', Float, nullable=False))
Index('mq_resource_tenant_kind', resources.c.tenant_id, resources.c.kind)
audit_events = Table('mq_audit_events', metadata,
    Column('id', String(64), primary_key=True), Column('tenant_id', String(64), nullable=False),
    Column('actor_id', String(64), nullable=False), Column('action', String(100), nullable=False),
    Column('resource_kind', String(48), nullable=False), Column('resource_id', String(160), nullable=False),
    Column('details', Text, nullable=False, default='{}'), Column('created_at', Float, nullable=False))
login_attempts = Table('mq_login_attempts', metadata,
    Column('id', String(64), primary_key=True), Column('identity_hash', String(64), nullable=False),
    Column('created_at', Float, nullable=False))
Index('mq_login_window', login_attempts.c.identity_hash, login_attempts.c.created_at)


def database_url():
    configured = os.environ.get('MATEQUILL_DATABASE_URL') or os.environ.get('TENANCY_DATABASE_URL')
    if configured:
        return configured.replace('postgresql://', 'postgresql+psycopg://', 1) if configured.startswith('postgresql://') else configured
    root = Path(__file__).resolve().parents[2] / 'runtime' / 'tenancy.sqlite3'
    root.parent.mkdir(parents=True, exist_ok=True)
    return 'sqlite:///' + root.as_posix()


@lru_cache(maxsize=8)
def _engine(url):
    options = {'pool_pre_ping': True}
    if url.startswith('sqlite:'):
        options['connect_args'] = {'check_same_thread': False, 'timeout': 15}
    engine = create_engine(url, **options)
    if engine.dialect.name == 'sqlite':
        metadata.create_all(engine)
    elif engine.dialect.name == 'postgresql':
        with engine.begin() as connection:
            role = connection.execute(text('SELECT rolsuper, rolbypassrls FROM pg_roles WHERE rolname=current_user')).first()
            if not role or role.rolsuper or role.rolbypassrls:
                raise RuntimeError('Use a non-superuser PostgreSQL application role without BYPASSRLS')
            owner = connection.execute(text("SELECT 1 FROM pg_class c JOIN pg_roles r ON c.relowner=r.oid WHERE c.relname IN ('mq_resources','mq_audit_events') AND r.rolname=current_user LIMIT 1")).first()
            if owner:
                raise RuntimeError('Application role must not own tenant business tables; run migrations with a separate owner')
            if connection.execute(text("SELECT to_regclass('mq_resources')")).scalar() is None:
                raise RuntimeError('Run Alembic migrations before starting MateQuill')
            policies = connection.execute(text("SELECT relname,relrowsecurity,relforcerowsecurity FROM pg_class WHERE relname IN ('mq_resources','mq_audit_events')")).all()
            if len(policies) != 2 or any(not row.relrowsecurity or not row.relforcerowsecurity for row in policies):
                raise RuntimeError('Tenant business tables require enabled and forced row security')
    return engine


def get_engine():
    return _engine(database_url())


@contextmanager
def db_connection(*, tenant_id=None):
    from .context import get_context
    context = get_context()
    effective_tenant = tenant_id or (context.tenant_id if context else '')
    active = _transaction_connection.get()
    if active is not None:
        connection, transaction_tenant = active
        if transaction_tenant != effective_tenant:
            raise RuntimeError('Cannot change tenant inside a business transaction')
        yield connection
        return
    with get_engine().begin() as connection:
        if connection.dialect.name == 'postgresql':
            connection.execute(text("SELECT set_config('app.tenant_id', :tenant, true)"),
                               {'tenant': effective_tenant})
            connection.execute(text("SELECT set_config('app.user_id', :actor, true)"),
                               {'actor': context.user_id if context else ''})
        yield connection


@contextmanager
def transaction_context():
    """Atomically compose repository helpers under one immutable tenant scope."""
    from .context import get_context
    context = get_context()
    active = _transaction_connection.get()
    if active is not None:
        yield active[0]
        return
    with db_connection() as connection:
        token = _transaction_connection.set((connection, context.tenant_id if context else ''))
        try:
            yield connection
        finally:
            _transaction_connection.reset(token)


def clear_engine_cache():
    # Test helper: existing pooled connections must not outlive a temporary DB.
    _engine.cache_clear()
