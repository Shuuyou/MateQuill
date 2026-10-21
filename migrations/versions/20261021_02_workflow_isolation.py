"""Workflow usage isolation and operation-specific partner grant policies.

Revision ID: 20261021_02
"""
from alembic import op
from sqlalchemy import text
from app.tenancy.database import metadata

revision = '20261021_02'
down_revision = '20261021_01'
branch_labels = None
depends_on = None


def upgrade():
    bind = op.get_bind()
    # These tables are registered by the workflow modules imported in env.py.
    for name in ('mq_job_dispatch', 'mq_usage_ledger', 'mq_channel_grants'):
        if name in metadata.tables:
            metadata.tables[name].create(bind, checkfirst=True)
    if bind.dialect.name != 'postgresql':
        return
    if 'mq_usage_ledger' in metadata.tables:
        op.execute('ALTER TABLE mq_usage_ledger ENABLE ROW LEVEL SECURITY')
        op.execute('ALTER TABLE mq_usage_ledger FORCE ROW LEVEL SECURITY')
        op.execute("CREATE POLICY mq_tenant_isolation ON mq_usage_ledger USING (tenant_id = current_setting('app.tenant_id', true)) WITH CHECK (tenant_id = current_setting('app.tenant_id', true))")
    if 'mq_channel_grants' in metadata.tables:
        op.execute('ALTER TABLE mq_channel_grants ENABLE ROW LEVEL SECURITY')
        op.execute('ALTER TABLE mq_channel_grants FORCE ROW LEVEL SECURITY')
        op.execute('DROP POLICY IF EXISTS mq_tenant_isolation ON mq_channel_grants')
        shared = "sender_tenant_id = current_setting('app.tenant_id', true) OR recipient_tenant_id = current_setting('app.tenant_id', true)"
        sender = "sender_tenant_id = current_setting('app.tenant_id', true)"
        op.execute(f'CREATE POLICY mq_grants_read ON mq_channel_grants FOR SELECT USING ({shared})')
        op.execute(f'CREATE POLICY mq_grants_insert ON mq_channel_grants FOR INSERT WITH CHECK ({sender})')
        op.execute(f'CREATE POLICY mq_grants_update ON mq_channel_grants FOR UPDATE USING ({shared}) WITH CHECK ({shared})')
        op.execute(f'CREATE POLICY mq_grants_delete ON mq_channel_grants FOR DELETE USING ({sender})')


def downgrade():
    raise RuntimeError('Review partner sharing and usage retention before removing workflow isolation')
