"""Exercise the real shell during slow session loading and sidebar transitions.

Uses an existing local QA session; never creates users or submits content.
"""
import asyncio
import json
from pathlib import Path

from playwright.async_api import async_playwright

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / 'output/ui-shell-controls-20261023'


async def main():
    OUT.mkdir(parents=True, exist_ok=True)
    cookies = json.loads((ROOT / 'runtime/opportunity-verification-cookies.json').read_text(encoding='utf-8'))
    checks = {}
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        context = await browser.new_context(viewport={'width': 1440, 'height': 1000},
            reduced_motion='no-preference', record_video_dir=str(OUT / 'recordings'))
        await context.add_cookies([{k: v for k, v in item.items() if k in ('name', 'value', 'domain', 'path')}
                                  for item in cookies])
        page = await context.new_page()
        errors, mutations = [], []
        page.on('pageerror', lambda error: errors.append(str(error)))
        page.on('request', lambda request: mutations.append(request.url.split('?')[0])
                if '/api/' in request.url and request.method not in ('GET', 'HEAD', 'OPTIONS') else None)

        async def delayed_session(route):
            await asyncio.sleep(2)
            await route.continue_()

        await page.route('**/api/auth/me', delayed_session)
        await page.goto('http://127.0.0.1:3003/', wait_until='domcontentloaded', timeout=90000)
        loader = page.locator('.mq-workspace-loading .youju-character')
        await loader.wait_for()
        box = await loader.bounding_box()
        assert box and box['width'] == 56 and box['height'] == 56, box
        checks['slow_start_mascot_px'] = box
        assert await loader.get_attribute('width') == '64'
        assert await loader.get_attribute('height') == '64'
        checks['thinking_animation'] = await loader.locator('.yj-head').evaluate('(e)=>getComputedStyle(e).animationName')
        assert checks['thinking_animation'] == 'yj-think'
        await page.screenshot(path=str(OUT / 'startup.png'))
        await page.locator('.mq-sidebar').wait_for(timeout=30000)
        await page.unroute('**/api/auth/me', delayed_session)
        brand = page.locator('.mq-sidebar-toggle')
        mascot = page.locator('.mq-brand .youju-character')
        assert await mascot.evaluate('(e)=>getComputedStyle(e).opacity') == '1'
        sidebar = page.locator('.mq-sidebar')
        checks['initial_sidebar_expanded'] = await brand.get_attribute('aria-expanded')
        if checks['initial_sidebar_expanded'] == 'true':
            await brand.click()
            await page.wait_for_timeout(400)
        await page.mouse.move(500, 100)
        await page.wait_for_timeout(400)
        initial_sidebar = await sidebar.bounding_box()
        assert round(initial_sidebar['width']) == 82, initial_sidebar
        await brand.hover()
        await page.wait_for_timeout(300)
        checks['logo_visible_with_toggle_hover'] = await mascot.evaluate('(e)=>getComputedStyle(e).opacity')
        assert checks['logo_visible_with_toggle_hover'] == '1'
        await brand.click()
        await page.wait_for_timeout(70)
        middle = await page.evaluate('''()=>({width:document.querySelector('.mq-sidebar').getBoundingClientRect().width,
            padding:parseFloat(getComputedStyle(document.querySelector('.mq-app')).paddingLeft)})''')
        padding = middle['padding']
        checks['opening_intermediate_width'] = middle['width']
        checks['opening_content_padding'] = padding
        assert 82 < middle['width'] < 220, middle
        assert abs(middle['width'] - padding) < 3, (middle, padding)
        await page.screenshot(path=str(OUT / 'sidebar-opening.png'))
        await page.wait_for_timeout(400)
        assert round((await sidebar.bounding_box())['width']) == 220
        assert await page.locator('.mq-brand-wordmark').evaluate('(e)=>getComputedStyle(e).opacity') == '1'
        assert await mascot.evaluate('(e)=>getComputedStyle(e).opacity') == '1'
        logo_box = await page.locator('.mq-brand').bounding_box()
        toggle_box = await brand.bounding_box()
        assert logo_box['x'] + logo_box['width'] <= toggle_box['x'] + 1
        attachment = page.get_by_role('button', name='上传录音文件', exact=True)
        assert await attachment.locator('svg').count() == 1
        assert (await attachment.inner_text()).strip() == ''
        assert await attachment.get_attribute('aria-expanded') == 'false'
        await attachment.click()
        await page.get_by_role('button', name='上传录音', exact=True).wait_for()
        attachment = page.get_by_role('button', name='收起录音文件选项', exact=True)
        assert await attachment.get_attribute('aria-expanded') == 'true'
        checks['recording_upload_toggle_works'] = True
        await page.screenshot(path=str(OUT / 'recording-upload.png'))
        await attachment.click()
        assert await page.get_by_role('button', name='上传录音', exact=True).count() == 0
        assert await page.locator('.mq-composer-toolbar button[type="submit"] svg').count() == 1
        await page.screenshot(path=str(OUT / 'sidebar-expanded.png'))
        await brand.click()
        await page.wait_for_timeout(70)
        checks['closing_intermediate_width'] = (await sidebar.bounding_box())['width']
        assert 82 < checks['closing_intermediate_width'] < 220
        await page.wait_for_timeout(400)
        await page.mouse.move(500, 100)
        await page.screenshot(path=str(OUT / 'sidebar-collapsed.png'))
        await page.emulate_media(reduced_motion='reduce')
        checks['reduced_motion_transition'] = await sidebar.evaluate('(e)=>getComputedStyle(e).transitionDuration')
        assert all(float(v.strip().removesuffix('s')) == 0 for v in checks['reduced_motion_transition'].split(','))
        await page.set_viewport_size({'width': 390, 'height': 844})
        await page.wait_for_timeout(300)
        checks['mobile_label_opacity'] = await page.locator('.mq-sidebar nav button span').first.evaluate('(e)=>getComputedStyle(e).opacity')
        assert checks['mobile_label_opacity'] == '1'
        assert await page.evaluate('document.documentElement.scrollWidth <= innerWidth')
        await page.screenshot(path=str(OUT / 'mobile.png'))
        assert not errors, errors
        assert not mutations, mutations
        checks.update(page_errors=errors, mutating_requests=mutations, passed=True)
        (OUT / 'verification.json').write_text(json.dumps(checks, ensure_ascii=False, indent=2), encoding='utf-8')
        await context.close()
        await browser.close()
    print(json.dumps(checks, ensure_ascii=False))


if __name__ == '__main__':
    asyncio.run(main())
