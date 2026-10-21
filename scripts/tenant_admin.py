"""Provision a business workspace locally; never exposes public admin signup.

Run `python scripts/tenant_admin.py bootstrap --email ... --name ... --tenant ...`.
The password is hidden input. MATEQUILL_DATABASE_URL selects the prepared runtime
database; production migrations use a separate owner connection.
"""
import argparse
import getpass
import sys
import time
from pathlib import Path
from uuid import uuid4

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from sqlalchemy import select
from app.tenancy.auth import normalize_email, password_hash, verify_password
from app.tenancy.database import db_connection, tenants, users, memberships


def bootstrap(*, email, name, tenant_name, password, tenant_id=None):
    email = normalize_email(email)
    encoded = password_hash(password)
    now = time.time()
    with db_connection() as connection:
        user = connection.execute(select(users).where(users.c.email == email)).mappings().first()
        if user:
            if not verify_password(password, user['password_hash']):
                raise ValueError('Existing account requires its current password')
            user_id = user['id']
        else:
            user_id = uuid4().hex
            connection.execute(users.insert().values(id=user_id, email=email, name=name,
                password_hash=encoded, active=True, created_at=now))
        identifier = tenant_id or uuid4().hex
        existing = connection.execute(select(tenants).where(tenants.c.id == identifier)).first()
        if not existing:
            connection.execute(tenants.insert().values(id=identifier, name=tenant_name, created_at=now))
        already = connection.execute(select(memberships.c.id).where(memberships.c.user_id == user_id,
            memberships.c.tenant_id == identifier, memberships.c.role == 'admin', memberships.c.store_id.is_(None))).first()
        if already:
            connection.execute(memberships.update().where(memberships.c.id == already.id).values(active=True))
        else:
            connection.execute(memberships.insert().values(id=uuid4().hex, user_id=user_id,
                tenant_id=identifier, store_id=None, role='admin', active=True, created_at=now))
    return {'tenant_id': identifier, 'user_id': user_id, 'email': email}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest='command', required=True)
    create = sub.add_parser('bootstrap')
    create.add_argument('--email', required=True)
    create.add_argument('--name', required=True)
    create.add_argument('--tenant', required=True)
    create.add_argument('--tenant-id')
    args = parser.parse_args()
    password = getpass.getpass('Workspace administrator password (12+ characters): ')
    repeated = getpass.getpass('Repeat password: ')
    if password != repeated:
        parser.error('Passwords do not match')
    result = bootstrap(email=args.email, name=args.name, tenant_name=args.tenant,
                       password=password, tenant_id=args.tenant_id)
    password = repeated = None
    print('Workspace created:', result['tenant_id'], 'administrator:', result['email'])


if __name__ == '__main__':
    main()
