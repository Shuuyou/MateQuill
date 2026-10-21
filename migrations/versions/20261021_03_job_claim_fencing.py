"""Fenced generation job leases.

Revision ID: 20261021_03
"""
from alembic import op
import sqlalchemy as sa

revision = '20261021_03'
down_revision = '20261021_02'
branch_labels = None
depends_on = None


def upgrade():
    bind = op.get_bind()
    columns = {item['name'] for item in sa.inspect(bind).get_columns('mq_job_dispatch')}
    if 'claim_token' not in columns:
        op.add_column('mq_job_dispatch', sa.Column('claim_token', sa.String(64), nullable=True))


def downgrade():
    raise RuntimeError('Drain generation workers before removing fenced job leases')
