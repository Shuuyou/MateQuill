"""DDL is executed only by the migration owner, never by a runtime request."""
import os
from alembic import context
from sqlalchemy import create_engine
from app.tenancy.database import metadata

# Load optional business tables into the one migration metadata graph.
try:
    import app.channel_workflows  # noqa: F401
except ImportError:
    pass
try:
    import app.generation_jobs  # noqa: F401
except ImportError:
    pass

config = context.config
url = os.environ.get('MATEQUILL_MIGRATION_DATABASE_URL') or os.environ.get('MATEQUILL_DATABASE_URL')
if not url:
    raise RuntimeError('Set MATEQUILL_MIGRATION_DATABASE_URL to the migration-owner connection')
if url.startswith('postgresql://'):
    url = url.replace('postgresql://', 'postgresql+psycopg://', 1)

if context.is_offline_mode():
    context.configure(url=url, target_metadata=metadata, literal_binds=True)
    with context.begin_transaction():
        context.run_migrations()
else:
    with create_engine(url).connect() as connection:
        context.configure(connection=connection, target_metadata=metadata)
        with context.begin_transaction():
            context.run_migrations()
