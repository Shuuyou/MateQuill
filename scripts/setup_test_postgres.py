"""Create an ephemeral owner/app pair and migrate a CI PostgreSQL database.

Requires MATEQUILL_TEST_ADMIN_URL (CI-local superuser service connection). Writes
the generated non-owner test URL to GITHUB_ENV, or an explicitly requested ignored
local --env-file. No connection URLs/passwords are printed or committed.
"""
import argparse
import os
from pathlib import Path
import secrets
import subprocess
import sys

from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url

ROOT = Path(__file__).resolve().parents[1]


def prepare():
    target = os.environ.get('MATEQUILL_TEST_ADMIN_URL')
    if not target:
        raise RuntimeError('MATEQUILL_TEST_ADMIN_URL is required')
    if target.startswith('postgresql://'):
        target = target.replace('postgresql://', 'postgresql+psycopg://', 1)
    admin_url = make_url(target)
    suffix = secrets.token_hex(6)
    owner = 'mq_ci_owner_' + suffix
    runtime = 'mq_ci_app_' + suffix
    database = 'mq_ci_' + suffix
    owner_password = secrets.token_urlsafe(36)
    runtime_password = secrets.token_urlsafe(36)
    engine = create_engine(admin_url, isolation_level='AUTOCOMMIT')
    # Only generated hex identifiers and URL-safe generated passwords enter DDL.
    with engine.connect() as connection:
        connection.exec_driver_sql(f"CREATE ROLE {owner} LOGIN PASSWORD '{owner_password}' NOSUPERUSER NOBYPASSRLS")
        connection.exec_driver_sql(f"CREATE ROLE {runtime} LOGIN PASSWORD '{runtime_password}' NOSUPERUSER NOBYPASSRLS")
        connection.exec_driver_sql(f'CREATE DATABASE {database} OWNER {owner}')
    owner_url = admin_url.set(username=owner, password=owner_password, database=database)
    app_url = admin_url.set(username=runtime, password=runtime_password, database=database)
    environment = dict(os.environ, MATEQUILL_MIGRATION_DATABASE_URL=owner_url.render_as_string(hide_password=False))
    subprocess.run([sys.executable, '-m', 'alembic', 'upgrade', 'head'], cwd=ROOT,
                   env=environment, check=True)
    with create_engine(owner_url).begin() as connection:
        connection.exec_driver_sql(f'GRANT CONNECT ON DATABASE {database} TO {runtime}')
        connection.exec_driver_sql(f'GRANT USAGE ON SCHEMA public TO {runtime}')
        connection.exec_driver_sql(f'GRANT SELECT,INSERT,UPDATE,DELETE ON ALL TABLES IN SCHEMA public TO {runtime}')
        connection.exec_driver_sql(f'GRANT USAGE,SELECT ON ALL SEQUENCES IN SCHEMA public TO {runtime}')
        connection.exec_driver_sql(f'ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT SELECT,INSERT,UPDATE,DELETE ON TABLES TO {runtime}')
    return app_url.render_as_string(hide_password=False)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--env-file', type=Path)
    args = parser.parse_args()
    destination = args.env_file or (Path(os.environ['GITHUB_ENV']) if os.environ.get('GITHUB_ENV') else None)
    if destination is None:
        parser.error('Set GITHUB_ENV or provide --env-file under ignored runtime/')
    if args.env_file:
        path = args.env_file.resolve()
        if not path.is_relative_to((ROOT / 'runtime').resolve()):
            parser.error('Local connection files must stay in ignored runtime/')
        path.parent.mkdir(parents=True, exist_ok=True)
    try:
        url = prepare()
    except Exception as exc:
        # SQL errors can contain DDL credentials; report only a safe category.
        print('CI database preparation failed: ' + type(exc).__name__, file=sys.stderr)
        return 1
    with destination.open('a', encoding='utf-8') as stream:
        stream.write('MATEQUILL_TEST_POSTGRES_URL=' + url + '\n')
    print('Ephemeral PostgreSQL migrated with separate owner and restricted application roles.')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
