"""Provision clearly labelled local demo businesses with random private passwords.

Connection URL comes from MATEQUILL_DATABASE_URL. Passwords are written only to
an ignored runtime file; neither credentials nor invitation tokens are printed.
Existing seed output is reused, so rerunning does not create duplicate accounts.
"""
import json
import os
from pathlib import Path
import secrets
import sys
import time
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def main():
    configuration = ROOT / 'runtime/tenancy-postgres.json'
    if configuration.is_file() and not os.getenv('MATEQUILL_DATABASE_URL'):
        os.environ['MATEQUILL_DATABASE_URL'] = json.loads(configuration.read_text())['app_url']
    # Import all tables before initial local SQLite provisioning.
    import app.api
    from scripts.tenant_admin import bootstrap
    from app.tenancy import TenantContext, set_context, reset_context
    from app.tenancy.auth import password_hash
    from app.tenancy.database import db_connection, users, memberships
    from app.store_campaigns import StoreInput, StoreRepository
    from app.channel_workflows import CampaignPackageInput, create_campaign, publish_campaign
    output = ROOT / 'runtime/demo-access.json'
    if output.exists():
        print('Demo access already provisioned:', output)
        return
    accounts = []
    primary_password = secrets.token_urlsafe(20)
    primary = bootstrap(email='hq@matequill.example', name='总部运营 · 演示',
        tenant_name='创非凡 · 演示企业', password=primary_password)
    accounts.append({**primary, 'name': '总部运营', 'password': primary_password})
    partner_password = secrets.token_urlsafe(20)
    partner = bootstrap(email='partner@matequill.example', name='伙伴管理员 · 演示',
        tenant_name='合作经销商 · 演示企业', password=partner_password)
    accounts.append({**partner, 'name': '伙伴管理员', 'password': partner_password})
    store_sets = {}
    for organization, names in [(primary, [('校园店 · 演示', '大学生与新生家长'),
                                             ('商务店 · 演示', '差旅办公与企业采购顾客'),
                                             ('创作店 · 演示', '设计与内容创作者')]),
                                 (partner, [('伙伴门店 · 演示', '周边社区家庭')])]:
        token = set_context(TenantContext(user_id=organization['user_id'], tenant_id=organization['tenant_id'],
            roles=('admin',), all_stores=True, grants=(('admin', None),)))
        try:
            stores = [StoreRepository().save_store(StoreInput(name=name, audience=audience,
                core_tasks=['回答真实顾客问题', '准备新品到店体验内容'])) for name, audience in names]
            store_sets[organization['tenant_id']] = stores
            if organization == primary:
                package = create_campaign(CampaignPackageInput(title='新品生态体验周 · 演示',
                    brief='围绕本店顾客的真实学习或工作任务，介绍华为电脑与手机、平板的使用场景。',
                    product='HUAWEI MateBook Pro', ecosystem_products=['HUAWEI Mate 80', 'HUAWEI MatePad Edge'],
                    store_ids=[store['id'] for store in stores]))
                publish_campaign(package['id'])
        finally:
            reset_context(token)
    for email, name, role, store in [
        ('manager@matequill.example', '校园店长', 'store_manager', store_sets[primary['tenant_id']][0]),
        ('campus@matequill.example', '校园店员', 'salesperson', store_sets[primary['tenant_id']][0]),
        ('business@matequill.example', '商务店员', 'salesperson', store_sets[primary['tenant_id']][1])]:
        password = secrets.token_urlsafe(20)
        identifier = uuid4().hex
        with db_connection() as connection:
            connection.execute(users.insert().values(id=identifier, email=email, name=name + ' · 演示',
                password_hash=password_hash(password), active=True, created_at=time.time()))
            connection.execute(memberships.insert().values(id=uuid4().hex, user_id=identifier,
                tenant_id=primary['tenant_id'], store_id=store['id'], role=role, active=True, created_at=time.time()))
        accounts.append({'email': email, 'name': name, 'password': password,
            'tenant_id': primary['tenant_id'], 'store_id': store['id']})
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps({'label': '仅本机演示账号，请勿公开上传', 'accounts': accounts,
        'stores': store_sets}, ensure_ascii=False, indent=2), encoding='utf8')
    print('Provisioned 2 businesses, 4 stores and 5 demo accounts; private access:', output)


if __name__ == '__main__':
    main()
