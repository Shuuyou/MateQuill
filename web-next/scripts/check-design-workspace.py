"""Browser interaction contract test. Design responses are explicit UI fixtures.
Run against local Next/backend: python web-next/scripts/check-design-workspace.py
Requires playwright and local ignored demo-access.json; never prints credentials.
"""
import json
from pathlib import Path
from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parents[2]
account = next(a for a in json.loads((ROOT / 'runtime/demo-access.json').read_text(encoding='utf-8'))['accounts'] if a['email'].startswith('campus@'))
bundle = {
    'id': 'ui-fixture-design', 'title': '界面契约测试 · 两张图片', 'version': 1, 'platform': 'rednote',
    'artboards': [{'id': f'board-{i}', 'role': 'cover' if i == 1 else 'detail', 'title': f'测试画板 {i}', 'preview_url': f'/api/ui-fixture/{i}.svg', 'export_urls': {'png': f'/api/ui-fixture/{i}.png', 'svg': f'/api/ui-fixture/{i}.svg'}} for i in (1, 2)],
}
with sync_playwright() as p:
    browser = p.chromium.launch(headless=True)
    page = browser.new_page(viewport={'width': 1440, 'height': 1050})
    errors, requests = [], []
    page.on('pageerror', lambda error: errors.append(str(error)))
    def create(route):
        requests.append({'url': route.request.url, 'body': route.request.post_data_json})
        if route.request.url.endswith('/revise'):
            bundle['version'] = 2
        route.fulfill(status=202, json={'id': 'ui-fixture-job', 'status': 'queued'})
    page.route('**/api/designs', create)
    page.route('**/api/designs/ui-fixture-design/revise', create)
    page.route('**/api/designs/ui-fixture-design', lambda route: route.fulfill(json=bundle))
    page.route('**/api/jobs/ui-fixture-job', lambda route: route.fulfill(json={'id': 'ui-fixture-job', 'status': 'succeeded', 'result': bundle}))
    page.route('**/api/ui-fixture/*', lambda route: route.fulfill(content_type='image/svg+xml', body='<svg xmlns="http://www.w3.org/2000/svg" width="1080" height="1440"><rect width="1080" height="1440" fill="#e6edcf"/><text x="110" y="300" font-size="80">UI fixture</text></svg>'))
    page.goto('http://127.0.0.1:3003/', wait_until='domcontentloaded')
    page.get_by_role('button', name='已有账号，登录继续', exact=True).click(timeout=30000)
    page.locator('input[name="email"]').fill(account['email'])
    page.locator('input[name="password"]').fill(account['password'])
    page.get_by_role('button', name='进入工作空间').click()
    page.get_by_role('button', name='打开这篇 ↗').click(timeout=30000)
    page.get_by_role('button', name='为这篇内容设计一组图片').click()
    page.get_by_role('button', name='放大查看：测试画板 1').wait_for()
    assert len(requests) == 1
    heading = page.get_by_role('heading', name=bundle['title'])
    assert 0 <= heading.bounding_box()['y'] < 110, 'Workbench must escape transformed page ancestors'
    page.get_by_role('button', name='放大查看：测试画板 1').click()
    page.get_by_role('dialog').wait_for()
    page.keyboard.press('Escape')
    assert page.get_by_role('dialog').count() == 0
    page.get_by_role('button', name='测试画板 2', exact=False).click()
    assert '测试画板 2' in page.get_by_role('button', name='放大查看：测试画板 2').get_attribute('aria-label')
    page.get_by_label('想再调整哪里？').fill('标题再轻一点')
    page.get_by_role('button', name='修改图片', exact=True).click()
    page.wait_for_function('!document.querySelector("[aria-busy=true]")')
    assert len(requests) == 2
    assert requests[1]['body']['instruction'] == '标题再轻一点'
    assert requests[0]['body']['idempotency_key'] != requests[1]['body']['idempotency_key']
    page.get_by_role('button', name='← 返回内容', exact=True).click()
    page.get_by_role('button', name='为这篇内容设计一组图片').click()
    page.get_by_role('button', name='放大查看：测试画板 1').wait_for()
    assert len(requests) == 2, 'Reopening must reuse completed design'
    assert not errors, errors
    print(json.dumps({'fixture_ui_test': 'passed', 'create_requests': 1, 'revision_requests': 1, 'reopen_regenerated': False, 'console_errors': errors}))
    browser.close()
