"""Opt-in public demonstration identities, isolated from customer tenants."""
import os
import time
from threading import Lock
from fastapi import HTTPException
from sqlalchemy import select
from .tenancy import TenantContext, set_context, reset_context, transaction_context
from .tenancy.auth import _DUMMY_HASH
from .tenancy.database import tenants, users, memberships, resources
from .store_campaigns import StoreInput, StoreRepository

TENANT = 'matequill-public-demo-v1'
PERSONAS = (
    {'id': 'hq', 'label': '省代总部运营', 'description': '统筹推广与多门店协同', 'role': 'operator'},
    {'id': 'fuleide', 'label': '福雷德广场店', 'description': '下沙高教园 · 校园生活', 'role': 'salesperson', 'location': '杭州市钱塘区白杨街道福雷德广场，学林街与文泽路附近'},
    {'id': 'raffles', 'label': '杭州来福士店', 'description': '钱江新城 · 都市通勤', 'role': 'salesperson', 'location': '杭州市上城区新业路杭州来福士中心'},
    {'id': 'ifs', 'label': '长沙国金店', 'description': '五一商圈 · 城市影像', 'role': 'salesperson', 'location': '长沙市芙蓉区解放西路长沙国金中心'},
)
_lock = Lock()

def enabled():
    return os.getenv('MATEQUILL_DEMO_ACCOUNTS', '0') == '1'

def choices():
    return [{'id': p['id'], 'label': p['label'], 'description': p['description']} for p in PERSONAS] if enabled() else []

def demo_identity(identifier):
    if not enabled():
        raise HTTPException(404, '演示入口未启用')
    chosen = next((p for p in PERSONAS if p['id'] == identifier), None)
    if not chosen:
        raise HTTPException(422, '请选择演示门店')
    context = TenantContext('demo-hq-v1', TENANT, roles=('operator',), all_stores=True, grants=(('operator', None),))
    token = set_context(context)
    try:
        with _lock, transaction_context() as connection:
            tenant = connection.execute(select(tenants).where(tenants.c.id == TENANT)).mappings().first()
            if not tenant:
                connection.execute(tenants.insert().values(id=TENANT, name='MateQuill · 渠道演示', created_at=time.time()))
            elif tenant['name'] != 'MateQuill · 渠道演示':
                raise HTTPException(409, '演示空间标识冲突')
            store_rows = connection.execute(select(resources.c.id, resources.c.body).where(resources.c.kind == 'store', resources.c.tenant_id == TENANT)).mappings().all()
            import json
            stores = {json.loads(r['body'])['name']: r['id'] for r in store_rows}
            result = None
            for persona in PERSONAS:
                uid = 'demo-' + persona['id'] + '-v1'
                if not connection.execute(select(users.c.id).where(users.c.id == uid)).first():
                    connection.execute(users.insert().values(id=uid, email=uid+'@demo.matequill.invalid', name=persona['label'], password_hash=_DUMMY_HASH, active=True, created_at=time.time()))
                sid = None
                if persona['id'] != 'hq':
                    sid = stores.get(persona['label'])
                    if not sid:
                        sid = StoreRepository().save_store(StoreInput(name=persona['label'], location=persona['location']))['id']
                        stores[persona['label']] = sid
                mid = 'public-demo-' + persona['id'] + '-v1'
                if not connection.execute(select(memberships.c.id).where(memberships.c.id == mid)).first():
                    connection.execute(memberships.insert().values(id=mid, user_id=uid, tenant_id=TENANT, store_id=sid, role=persona['role'], active=True, created_at=time.time()))
                if persona['id'] == identifier:
                    result = (uid, TENANT, sid)
            return result
    finally:
        reset_context(token)
