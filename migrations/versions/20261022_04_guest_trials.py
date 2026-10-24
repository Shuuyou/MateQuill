"""Private, expiring visitor workspaces on the existing authorization model."""
from alembic import op
from app.tenancy.database import trials

revision = '20261022_04'
down_revision = '20261021_03'
branch_labels = None
depends_on = None


def upgrade():
    trials.create(op.get_bind(), checkfirst=True)


def downgrade():
    raise RuntimeError('Preserve trial identity and claims when rolling back the UI')
