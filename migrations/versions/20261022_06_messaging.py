"""Messaging identity and delivery locators, without customer message bodies."""
from alembic import op
from app.messaging.tables import TABLES

revision = '20261022_06'
down_revision = '20261022_05'
branch_labels = None
depends_on = None


def upgrade():
    for table in TABLES:
        table.create(op.get_bind(), checkfirst=True)


def downgrade():
    raise RuntimeError('Preserve bindings and delivery receipts before rollback')
