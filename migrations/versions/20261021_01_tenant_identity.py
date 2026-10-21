"""Tenant identity, resource ownership and database isolation.

Revision ID: 20261021_01
"""
from alembic import op
from sqlalchemy import text
from app.tenancy.database import metadata

revision = '20261021_01'
down_revision = None
branch_labels = None
depends_on = None


def upgrade():
    bind = op.get_bind()
    metadata.create_all(bind)
    if bind.dialect.name == 'postgresql':
        tables = ['mq_resources', 'mq_audit_events']
        if 'mq_channel_grants' in metadata.tables:
            tables.append('mq_channel_grants')
        for table in tables:
            op.execute(f'ALTER TABLE {table} ENABLE ROW LEVEL SECURITY')
            op.execute(f'ALTER TABLE {table} FORCE ROW LEVEL SECURITY')
            predicate = "tenant_id = current_setting('app.tenant_id', true)"
            if table == 'mq_channel_grants':
                predicate = "sender_tenant_id = current_setting('app.tenant_id', true) OR recipient_tenant_id = current_setting('app.tenant_id', true)"
            op.execute(f'CREATE POLICY mq_tenant_isolation ON {table} USING ({predicate}) WITH CHECK ({predicate})')


def downgrade():
    # Explicit rollback only; no automatic dropping of business tables.
    raise RuntimeError('Export business data and use a reviewed recovery plan before removing tenancy tables')
