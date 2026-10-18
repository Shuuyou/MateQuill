"""Rasterize trusted, self-contained SVG with a browser, preserving real text."""
from __future__ import annotations


def render_png(svg: str, width: int, height: int) -> bytes:
    if not (320 <= width <= 4096 and 320 <= height <= 4096 and width * height <= 12_000_000):
        raise ValueError('画布尺寸超出渲染范围')
    from playwright.sync_api import sync_playwright
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        try:
            page = browser.new_page(viewport={'width': width, 'height': height}, device_scale_factor=1)
            page.route('**/*', lambda route: route.abort())
            page.set_content('<!doctype html><meta charset="utf-8"><style>html,body{margin:0;padding:0}svg{display:block}</style>'+svg,
                             wait_until='load', timeout=15000)
            page.evaluate('document.fonts.ready')
            return page.screenshot(type='png', clip={'x':0,'y':0,'width':width,'height':height}, timeout=25000)
        finally:
            browser.close()
