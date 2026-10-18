"""Read-only Huawei official specification snapshots with exact product identity."""
from __future__ import annotations

import json
import re
from html.parser import HTMLParser
from pathlib import Path


class _Node:
    def __init__(self, tag='', attrs=()):
        self.tag, self.attrs, self.children = tag, dict(attrs), []

    def walk(self):
        yield self
        for child in self.children:
            if isinstance(child, _Node):
                yield from child.walk()

    def has_class(self, name):
        return name in self.attrs.get('class', '').split()

    def text(self):
        if self.tag in ('script', 'style', 'noscript'):
            return ''
        value = ''.join(c.text() if isinstance(c, _Node) else c for c in self.children)
        return '\n' + value + '\n' if self.tag in ('div', 'p', 'li', 'br', 'h1', 'h2') else value


class _Tree(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.root = _Node()
        self.stack = [self.root]

    def handle_starttag(self, tag, attrs):
        node = _Node(tag, attrs)
        self.stack[-1].children.append(node)
        if tag not in ('area','base','br','col','embed','hr','img','input','link','meta','param','source','track','wbr'):
            self.stack.append(node)

    def handle_startendtag(self, tag, attrs):
        self.handle_starttag(tag, attrs)
        self.handle_endtag(tag)

    def handle_endtag(self, tag):
        for i in range(len(self.stack)-1, 0, -1):
            if self.stack[i].tag == tag:
                del self.stack[i:]
                break

    def handle_data(self, data):
        self.stack[-1].children.append(data)


def _clean(text):
    return '\n'.join(re.sub(r'\s+', ' ', line).strip() for line in text.splitlines() if line.strip())


def extract_specification(html: str, name: str) -> dict:
    """Only extract official specification accordion, never navigation/comparison cards."""
    tree = _Tree()
    tree.feed(html)
    sections = []
    for node in tree.root.walk():
        if not node.has_class('large-accordion__item'):
            continue
        title = next((n for n in node.walk() if n.has_class('large-accordion__title')), None)
        content = next((n for n in node.walk() if n.has_class('large-accordion__content')), None)
        if title is not None and content is not None:
            label, value = _clean(title.text()), _clean(content.text())
            if label and value:
                sections.append({'label': label, 'text': value})
    heading = next((_clean(n.text()) for n in tree.root.walk() if n.tag == 'h1'), '')
    markdown = '# ' + name + '\n\n' + '\n\n'.join('## ' + s['label'] + '\n\n' + s['text'] for s in sections)
    return {'page_heading': heading, 'sections': sections, 'text': markdown}


def load_official_catalog(path=None):
    path = Path(path) if path else Path(__file__).resolve().parent.parent/'data'/'official_products.json'
    if not path.exists():
        return []
    return json.loads(path.read_text(encoding='utf-8'))['products']


def extract_product_conditions(html: str) -> list[str]:
    """Keep original marketing footnotes; do not infer broad compatibility from imagery."""
    tree = _Tree()
    tree.feed(html)
    conditions = []
    for node in tree.root.walk():
        classes = node.attrs.get('class', '').split()
        selected = any(c in {'disclaimer-content','footnotes','note-list','section-footnote',
                             'mitchell-disclaimer','footernote','smcnote','pronote','note1','note-none'}
                       or re.fullmatch(r'(?:note-li|hw-footer-li)\d+', c) for c in classes)
        if selected:
            rows = [n for n in node.walk() if n.tag == 'li']
            if not rows:
                rows = [node]
            for row in rows:
                value = _clean(row.text())
                if value and value not in conditions:
                    conditions.append(value)
    return conditions
