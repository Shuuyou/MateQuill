"""Versioned creative-case connectors: cultural mechanisms, never product evidence.

The local connector is deliberately a research library, not a live trend feed.
It performs no network access, account scraping, or tenant resource discovery.
"""
from __future__ import annotations

from datetime import date, datetime
from functools import lru_cache
import json
from pathlib import Path
from typing import Literal, Protocol
from urllib.parse import urlparse
from zoneinfo import ZoneInfo

from pydantic import BaseModel, ConfigDict, Field, field_validator


class CreativeCase(BaseModel):
    model_config = ConfigDict(extra='forbid')
    id: str = Field(pattern=r'^[a-z][a-z0-9_.-]{2,79}$')
    title: str = Field(min_length=1, max_length=120)
    source_url: str = Field(max_length=500)
    source_name: str = Field(min_length=1, max_length=100)
    published_at: date
    publisher_identity: Literal['brand', 'agency', 'store', 'creator', 'platform']
    platforms: list[Literal['xiaohongshu', 'douyin', 'wechat', 'moments', 'poster']] = Field(min_length=1, max_length=5)
    audience_tags: list[str] = Field(min_length=1, max_length=12)
    mechanism: str = Field(min_length=10, max_length=350)
    visual_action: str = Field(min_length=10, max_length=350)
    participation: str = Field(min_length=10, max_length=300)
    applicable_contexts: list[str] = Field(min_length=1, max_length=8)
    transfer_method: str = Field(min_length=10, max_length=350)
    does_not_transfer: list[str] = Field(min_length=1, max_length=8)
    record_kind: Literal['research_case'] = 'research_case'

    @field_validator('source_url')
    @classmethod
    def source_is_public_reference(cls, value):
        parsed = urlparse(value)
        if (parsed.scheme != 'https' or parsed.hostname not in {
                'm.adquan.com', 'www.adquan.com', 'www.foodaily.com',
                'socialbeta.com', 'api.socialbeta.com'} or parsed.username or parsed.password
                or parsed.query or parsed.fragment):
            raise ValueError('Case sources must be canonical public research references')
        return value


class CaseRegistry(BaseModel):
    model_config = ConfigDict(extra='forbid')
    schema_version: Literal[1]
    registry_version: str = Field(pattern=r'^\d+\.\d+\.\d+$')
    updated_at: date
    scope: Literal['curated_research_mechanisms']
    cases: list[CreativeCase] = Field(min_length=1, max_length=100)

    @field_validator('cases')
    @classmethod
    def unique_cases(cls, cases):
        if len({case.id for case in cases}) != len(cases):
            raise ValueError('Creative case IDs must be unique')
        return cases


class CreativeCaseConnector(Protocol):
    connector_id: str
    def registry(self) -> CaseRegistry: ...


class LocalCreativeCaseConnector:
    connector_id = 'local.creative_cases.v1'

    def registry(self):
        # Return a fresh model so callers cannot mutate the shared research file.
        return _registry().model_copy(deep=True)


@lru_cache(maxsize=1)
def _registry():
    path = Path(__file__).resolve().parents[1] / 'data' / 'creative_cases.v1.json'
    return CaseRegistry.model_validate(json.loads(path.read_text(encoding='utf-8-sig')))


def _get(value, key, default=''):
    return value.get(key, default) if isinstance(value, dict) else getattr(value, key, default)


def _today(now):
    if now is None:
        return datetime.now(ZoneInfo('Asia/Shanghai')).date()
    if isinstance(now, datetime):
        if now.tzinfo is None:
            raise ValueError('Creative context datetime must include a timezone')
        return now.astimezone(ZoneInfo('Asia/Shanghai')).date()
    if isinstance(now, date):
        return now
    raise TypeError('now must be an aware datetime or a date')


def build_cultural_context(task, strategy=None, now=None) -> dict:
    """Select at most three relevant mechanisms without promoting old memes.

    Selection reads only arguments and the public versioned research registry.
    No user-supplied `signals` are elevated to authorized current events here.
    A separate live connector must supply its own authorization and provenance.
    """
    today = _today(now)
    connector = LocalCreativeCaseConnector()
    registry = connector.registry()
    platform = _get(task, 'platform', 'xiaohongshu')
    strategy = strategy or {}
    context = strategy.get('strategy_context') or {}
    if not isinstance(context, dict):
        context = {}
    publisher = context.get('publisher_identity', 'store')
    text = ' '.join(str(_get(task, field))[:1200] for field in ('audience', 'scene', 'goal'))
    # Historical conversation prose cannot drag the case selection toward an old topic.
    requirements = str(_get(task, 'requirements')).split('\n本店与本人的工作记忆', 1)[0]
    text += ' ' + requirements[:1200]
    eligible = [case for case in registry.cases if platform in case.platforms and case.published_at <= today]

    def score(case):
        audience_match = sum(3 for tag in case.audience_tags if tag in text)
        context_match = sum(2 for tag in case.applicable_contexts if tag in text)
        identity_match = 2 if case.publisher_identity == publisher else 0
        # Tie-break recent research examples; this is not a claim of live popularity.
        return (audience_match + context_match + identity_match, case.published_at, case.id)

    examples = []
    for case in sorted(eligible, key=score, reverse=True)[:3]:
        age = (today - case.published_at).days
        examples.append({**case.model_dump(mode='json'), 'age_days': age,
            'temporal_status': 'recent_reference' if age <= 45 else 'historical_mechanism',
            'is_current_trend': False, 'evidence_role': 'creative_method_only',
            'identity_transfer': 'same_identity' if case.publisher_identity == publisher else 'adapt_mechanism_to_publisher'})
    return {'connector_id': connector.connector_id, 'registry_version': registry.registry_version,
        'as_of': today.isoformat(), 'status': 'research_examples', 'live_retrieval': False,
        'research_examples': examples, 'current_trends': [],
        'use_in_generation': '用案例机制探索新的观察角度、画面动作与参与入口，按本次账号和已有产品事实重新创作。',
        'fact_boundary': '案例不是本次产品、门店或顾客的事实；原案例销量、活动、包装、台词与IP不会转移到新作品。',
        'coverage': '人工核对来源的公开案例库，不代表检索小红书或抖音全量内容，也不代表实时热榜。'}
