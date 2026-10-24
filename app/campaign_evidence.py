"""Source-backed campaign context, kept separate from publisher identity.

Campaigns are authored by an authorized operator. Evidence is resolved from the
server's current assignment, never from a client-authored requirements string.
"""
from __future__ import annotations

from datetime import date
import hashlib
from typing import Literal
from urllib.parse import urlsplit

from pydantic import BaseModel, ConfigDict, Field, model_validator, field_validator

from .models import Fact, Source


def public_reference_url(value: str) -> bool:
    try:
        parsed = urlsplit(value)
        host = (parsed.hostname or '').lower()
        domains = ('huawei.com', 'vmall.com', 'ithome.com', 'c114.com.cn',
                   'sina.com.cn', 'xinhuanet.com', 'news.cn', 'people.com.cn', 'cnr.cn')
        return bool(parsed.scheme == 'https' and not parsed.username and not parsed.password
                    and parsed.port in (None, 443)
                    and any(host == domain or host.endswith('.' + domain) for domain in domains))
    except ValueError:
        return False


class CampaignClaim(BaseModel):
    model_config = ConfigDict(extra='forbid')
    label: str = Field(min_length=1, max_length=160)
    value: str = Field(min_length=1, max_length=1500)
    quote: str = Field(min_length=1, max_length=3000)
    qualifiers: list[str] = Field(default_factory=list, max_length=12)

    @model_validator(mode='after')
    def exact_value(self):
        if not self.value.strip() or self.value not in self.quote:
            raise ValueError('活动事实的值需要能在原文摘录中定位')
        if any(len(value) > 500 for value in self.qualifiers):
            raise ValueError('活动限定条件过长')
        return self


class CampaignReference(BaseModel):
    model_config = ConfigDict(extra='forbid')
    title: str = Field(min_length=1, max_length=300)
    url: str = Field(min_length=8, max_length=2048)
    published_at: date | None = None
    excerpt: str = Field(min_length=1, max_length=14000)
    claims: list[CampaignClaim] = Field(default_factory=list, max_length=12)

    @field_validator('url')
    @classmethod
    def approved_url(cls, value):
        if not public_reference_url(value):
            raise ValueError('请使用华为官网或支持的公开媒体 HTTPS 来源')
        return value

    @model_validator(mode='after')
    def exact_quotes(self):
        if any(claim.quote not in self.excerpt for claim in self.claims):
            raise ValueError('活动事实需要逐字对应来源摘录')
        return self


def assignment_context(task) -> dict:
    """Obtain authorized, current execution context without trusting prompt text."""
    from .channel_workflows import _assignment
    from .tenancy import load_body
    identifier = getattr(task, 'assignment_id', '')
    if not identifier:
        return {}
    _, assignment, package = _assignment(identifier)
    store = load_body('store', assignment['store_id'])
    return {
        'publisher_identity': 'store',
        'campaign_publisher_identity': package.get('publisher_kind', 'regional'),
        'publisher_label': package.get('publisher_label', ''),
        'audience': task.audience, 'objective': task.goal,
        'platform': task.platform, 'store_id': assignment['store_id'],
        'store_context': {key: store.get(key) for key in ('audience', 'core_tasks', 'voice', 'location', 'materials')},
        'campaign_id': package['id'], 'campaign_version': package['version'],
        'is_simulation': package.get('is_simulation', False),
        'source_refs': package.get('source_refs', []),
    }


def append_campaign_evidence(task, bundle):
    if not getattr(task, 'assignment_id', '') or task.mode != 'local':
        return bundle
    context = assignment_context(task)
    for index, raw in enumerate(context.get('source_refs', [])):
        reference = CampaignReference.model_validate(raw)
        digest = hashlib.sha256((reference.url + reference.excerpt).encode()).hexdigest()
        sid = 'campaign-source-' + digest[:20]
        if sid not in {source.id for source in bundle.sources}:
            bundle.sources.append(Source(id=sid, title=reference.title, url=reference.url,
                provider='campaign_reference', excerpt=reference.excerpt,
                sha256=hashlib.sha256(reference.excerpt.encode()).hexdigest(),
                captured_at=reference.published_at.isoformat() if reference.published_at else ''))
        for claim_index, claim in enumerate(reference.claims):
            fid = 'FC' + hashlib.sha256((task.product + sid + str(claim_index)).encode()).hexdigest()[:16]
            if fid not in {fact.id for fact in bundle.facts}:
                bundle.facts.append(Fact(id=fid, product=task.product, source_id=sid,
                    label='活动/' + claim.label, value=claim.value, quote=claim.quote,
                    qualifiers=claim.qualifiers))
    # A campaign article supplements an existing product knowledge bundle. It
    # does not turn an unknown product or conflicted bundle into a verified one.
    return bundle
