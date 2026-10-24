"""One-click private trials and in-place conversion to an independent shop."""
import os
import time
from uuid import uuid4

from fastapi import HTTPException
from sqlalchemy import func, select, text, update

from .tenancy import TenantContext, set_context, reset_context, transaction_context
from .tenancy.auth import _DUMMY_HASH, password_hash
from .tenancy.database import memberships, tenants, trials, users
from .store_campaigns import StoreInput, StoreRepository

TRIAL_SECONDS = 24 * 60 * 60


def provision_trial():
    user_id, tenant_id = uuid4().hex, uuid4().hex
    token = set_context(TenantContext(user_id, tenant_id, roles=('admin',),
        all_stores=True, grants=(('admin', None),)))
    try:
        with transaction_context() as connection:
            # Pilot capacity is global, not a spoofable forwarded IP identity.
            if connection.dialect.name == 'postgresql':
                connection.execute(text("SELECT pg_advisory_xact_lock(hashtext('trial-admission'))"))
            else:
                connection.execute(update(tenants).where(tenants.c.id == '__trial_lock__').values(name=''))
            now = time.time()
            count = connection.execute(select(func.count()).select_from(trials).where(
                trials.c.created_at > now - TRIAL_SECONDS)).scalar_one()
            if count >= max(0, int(os.getenv('MATEQUILL_TRIAL_STARTS_PER_DAY', '200'))):
                raise HTTPException(429, '今天体验的人比较多，请稍后再来，已有账号可以直接登录')
            connection.execute(users.insert().values(id=user_id, email=user_id+'@guest.matequill.invalid',
                name='试用者', password_hash=_DUMMY_HASH, active=True, created_at=now))
            connection.execute(tenants.insert().values(id=tenant_id, name='我的创作', created_at=now))
            store = StoreRepository().save_store(StoreInput(name='我的创作', audience='按这次创作需求理解顾客'))
            connection.execute(memberships.insert().values(id=uuid4().hex, user_id=user_id,
                tenant_id=tenant_id, store_id=store['id'], role='salesperson', active=True, created_at=now))
            connection.execute(trials.insert().values(user_id=user_id, tenant_id=tenant_id,
                store_id=store['id'], created_at=now, expires_at=now+TRIAL_SECONDS))
        return user_id, tenant_id, store['id']
    finally:
        reset_context(token)


def claim_trial(context, email, password, store_name):
    encoded = password_hash(password)
    with transaction_context() as connection:
        # One conversion per visitor; retain the same owner/store/resource IDs.
        changed = connection.execute(update(trials).where(trials.c.user_id == context.user_id,
            trials.c.tenant_id == context.tenant_id, trials.c.claimed_at.is_(None),
            trials.c.expires_at > time.time()).values(claimed_at=time.time()))
        if changed.rowcount != 1:
            raise HTTPException(409, '这次体验已保存或已结束，请重新打开工作台')
        if email.endswith('@guest.matequill.invalid'):
            raise HTTPException(422, '请填写你日常使用的邮箱')
        existing = connection.execute(select(users.c.id).where(users.c.email == email)).first()
        if existing:
            raise HTTPException(409, '这个邮箱已有账号，请使用原账号登录')
        connection.execute(update(users).where(users.c.id == context.user_id).values(
            email=email, name='店长', password_hash=encoded))
        connection.execute(update(tenants).where(tenants.c.id == context.tenant_id).values(name=store_name))
        connection.execute(update(memberships).where(memberships.c.user_id == context.user_id,
            memberships.c.tenant_id == context.tenant_id).values(role='admin', store_id=None))
        # Trusted, narrowly scoped account conversion, not a public role switch.
        from .tenancy.database import resources
        import json
        row = connection.execute(select(resources).where(resources.c.kind == 'store',
            resources.c.id == context.store_id, resources.c.tenant_id == context.tenant_id)).mappings().one()
        body = {**json.loads(row['body']), 'name': store_name}
        connection.execute(update(resources).where(resources.c.kind == 'store',
            resources.c.id == context.store_id, resources.c.tenant_id == context.tenant_id)
            .values(body=json.dumps(body, ensure_ascii=False), version=resources.c.version+1, updated_at=time.time()))
