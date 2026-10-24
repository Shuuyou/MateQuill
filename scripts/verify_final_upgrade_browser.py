"""Inspect the real isolated preview; no browser responses or business results mocked."""
import asyncio
import json
from pathlib import Path
from playwright.async_api import async_playwright

ROOT = Path(__file__).resolve().parents[1]


async def main():
    metadata = json.loads((ROOT/'runtime/final-upgrade-preview.json').read_text(encoding='utf-8'))
    folder = Path(metadata['folder'])
    report = {'errors': [], 'mocked_requests': False}
    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch(headless=True)
        context = await browser.new_context(viewport={'width': 1440, 'height': 1000}, locale='zh-CN')
        await context.grant_permissions(['clipboard-read', 'clipboard-write'])
        await context.add_cookies(json.loads((folder/'acceptance-cookies.json').read_text(encoding='utf-8')))
        page = await context.new_page()
        page.on('pageerror', lambda error: report['errors'].append(str(error)))
        await page.goto(metadata['url'], wait_until='domcontentloaded', timeout=90000)
        opening = page.get_by_role('button', name='打开这篇', exact=False)
        await opening.wait_for(timeout=60000)
        await page.screenshot(path=str(folder/'daily-home.png'), full_page=True)
        report['homepage_ready'] = True
        await opening.click()
        body = page.get_by_role('article', name='朋友圈正文与配图').locator('textarea')
        await body.wait_for(timeout=60000)
        await page.get_by_role('button', name='复制文字', exact=False).click()
        await page.get_by_text('文字已复制', exact=True).wait_for(timeout=10000)
        copied = await page.evaluate('navigator.clipboard.readText()')
        report['copied_actual_text'] = copied.replace('\r\n', '\n') == (await body.input_value()).replace('\r\n', '\n')
        report['zero_image_download_hidden'] = await page.get_by_role('button', name='下载配图包', exact=False).count() == 0
        await page.wait_for_function('!document.getAnimations().some(a => a.playState === "running" && Number.isFinite(a.effect?.getComputedTiming().iterations))', timeout=10000)
        await page.screenshot(path=str(folder/'daily-opened.png'), full_page=True)
        report['adoption_opened'] = True
        report['url'] = page.url
        report['visible_text'] = (await page.locator('body').inner_text())[:12000]
        await browser.close()
    (folder/'browser-verification.json').write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps({key: value for key, value in report.items() if key != 'visible_text'}, ensure_ascii=True))


if __name__ == '__main__':
    asyncio.run(main())
