"""Durable Agent dispatch locators; content remains in scoped resources."""
from alembic import op
from app.gtm_agent.jobs import dispatch

revision = '20261022_05'
down_revision = '20261022_04'
branch_labels = None
depends_on = None


def upgrade():
    dispatch.create(op.get_bind(), checkfirst=True)


def downgrade():
    raise RuntimeError('Drain and preserve Agent turns before removing dispatch')
