"""Exact, evidence-bound exemptions for irrelevant composite-source conditions.

Original facts and qualifiers remain intact. An independent semantic reviewer
can mark a particular condition inapplicable to one complete cited surface;
any malformed, stale or ambiguous scope gives no exemptions at all.
"""
from __future__ import annotations

from typing import Literal
import re
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator


class ConditionScope(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)
    surface: str = Field(pattern=r'^(?:body:[0-9]+|card:[A-Za-z0-9_-]+|variant:[0-9]+)$', max_length=120)
    fact_id: str = Field(min_length=1, max_length=160)
    qualifier: str = Field(min_length=1, max_length=4000)
    claim_quote: str = Field(max_length=16000)
    applicable: Literal[False]
    reason: str = Field(min_length=1, max_length=1600)

    @field_validator('applicable', mode='before')
    @classmethod
    def exact_false(cls, value):
        if value is not False:
            raise ValueError('applicable must be the JSON boolean false')
        return value


CONDITION_SCOPE_SCHEMA = ConditionScope.model_json_schema()


def _value(obj):
    return obj.model_dump(mode='json') if hasattr(obj, 'model_dump') else obj


def _ids(row):
    value = row.get('fact_ids')
    return value if isinstance(value, list) and all(isinstance(x, str) for x in value) else []


def condition_surface_map(result) -> dict:
    """Stable surfaces with text identical to deterministic creative review."""
    result = _value(result)
    if not isinstance(result, dict):
        return {}
    surfaces = {}
    for index, block in enumerate(result.get('blocks') or []):
        block = _value(block)
        if isinstance(block, dict) and isinstance(block.get('text'), str):
            surfaces[f'body:{index}'] = {'text': block['text'], 'fact_ids': _ids(block),
                                        'disclosures': block.get('disclosures') or []}
    plan = result.get('creative_plan') or {}
    if not isinstance(plan, dict):
        return surfaces
    seen = set()
    for card in plan.get('cards') or []:
        if not isinstance(card, dict) or not isinstance(card.get('id'), str):
            continue
        key = 'card:' + card['id']
        if key in seen:
            # Duplicate IDs make the entire card target ambiguous.
            surfaces.pop(key, None)
            continue
        seen.add(key)
        text = '\n'.join(str(card.get(k, '')) for k in ('headline', 'body', 'caption', 'overlay_text') if card.get(k))
        surfaces[key] = {'text': text, 'fact_ids': _ids(card), 'disclosures': card.get('disclosures') or []}
    for index, variant in enumerate(plan.get('test_variants') or []):
        if isinstance(variant, dict):
            text = '\n'.join(str(variant.get(k, '')) for k in ('title', 'cover_copy') if variant.get(k))
            surfaces[f'variant:{index}'] = {'text': text, 'fact_ids': _ids(variant)}
    return surfaces


def validated_condition_exemptions(data, result, bundle) -> list[dict]:
    """Return all exact exemptions or none. This function never modifies evidence."""
    return inspect_condition_exemptions(data, result, bundle)[0]


def inspect_condition_exemptions(data, result, bundle) -> tuple[list[dict], dict]:
    """Safe persisted diagnostics distinguish missing output from rejected scope."""
    raw = data.get('condition_scope', []) if isinstance(data, dict) else []
    diagnostic = {'present': isinstance(data, dict) and 'condition_scope' in data,
                  'count': len(raw) if isinstance(raw, list) else None, 'accepted_count': 0}

    def reject(code, index=None, errors=None):
        diagnostic['rejection'] = {'code': code}
        if index is not None:
            diagnostic['rejection']['item_index'] = index
        if errors:
            diagnostic['rejection']['schema_errors'] = errors
        return [], diagnostic

    if not isinstance(data, dict) or data.get('verdict') not in {'supported', 'no_product_claims'}:
        return reject('semantic_verdict_not_supported')
    audit = data.get('audit')
    if not isinstance(audit, list):
        return reject('invalid_semantic_audit')
    for row in audit:
        if not isinstance(row, dict) or row.get('severity') not in {'info', 'warning', 'error'}:
            return reject('invalid_semantic_audit')
        if row.get('severity') == 'error':
            return reject('semantic_audit_has_error')
    raw = data.get('condition_scope', [])
    if not isinstance(raw, list) or len(raw) > 32:
        return reject('invalid_scope_container')
    surfaces = condition_surface_map(result)
    bundle = _value(bundle)
    if not isinstance(bundle, dict):
        return reject('invalid_evidence_bundle')
    facts = {}
    for fact in bundle.get('facts') or []:
        fact = _value(fact)
        if isinstance(fact, dict) and isinstance(fact.get('id'), str):
            if fact['id'] in facts:
                return reject('duplicate_evidence_fact')
            facts[fact['id']] = fact
    accepted, seen = [], set()
    for index, row in enumerate(raw):
        try:
            item = ConditionScope.model_validate(row)
        except ValidationError as exc:
            return reject('invalid_scope_schema', index,
                          [{'loc': list(e['loc']), 'type': e['type']} for e in exc.errors()][:10])
        except (TypeError, ValueError):
            return reject('invalid_scope_schema', index)
        if not item.qualifier.strip() or not item.reason.strip():
            return reject('empty_scope_semantics', index)
        surface, fact = surfaces.get(item.surface), facts.get(item.fact_id)
        key = (item.surface, item.fact_id, item.qualifier)
        if key in seen or surface is None or fact is None:
            return reject('duplicate_or_unknown_scope_reference', index)
        if item.claim_quote == '':
            # Stable surface ID resolves only this invocation's exact full text.
            # Persisted exemptions contain the text, never the empty indirection.
            item.claim_quote = surface['text']
        if (item.fact_id not in surface['fact_ids'] or item.qualifier not in (fact.get('qualifiers') or [])
                or item.claim_quote != surface['text']):
            return reject('scope_evidence_or_text_mismatch', index)
        if not item.claim_quote.strip():
            return reject('empty_claim_surface', index)
        seen.add(key)
        accepted.append(item.model_dump(mode='json'))
    diagnostic['accepted_count'] = len(accepted)
    return accepted, diagnostic


def missing_condition_candidates(result, bundle) -> list[dict]:
    """Enumerate actual missing qualifiers for review; never exempt automatically."""
    bundle = _value(bundle)
    if not isinstance(bundle, dict):
        return []
    facts = {f['id']: f for f in bundle.get('facts', []) if isinstance(f, dict) and isinstance(f.get('id'), str)}
    normalize = lambda text: re.sub(r'[\W_]+', '', text, flags=re.UNICODE).casefold()
    candidates = []
    for surface_id, surface in condition_surface_map(result).items():
        disclosure = surface.get('disclosures', [])
        public = surface['text'] + ' ' + ' '.join(x for x in disclosure if isinstance(x, str))
        for fact_id in surface['fact_ids']:
            fact = facts.get(fact_id, {})
            for qualifier in fact.get('qualifiers', []):
                if isinstance(qualifier, str) and qualifier and normalize(qualifier) not in normalize(public):
                    candidates.append({'surface': surface_id, 'fact_id': fact_id, 'qualifier': qualifier,
                                       'claim_quote': '', 'applicable': False,
                                       'instruction': '判断是否真正不相关；不相关才返回此精确ID/限定并说明理由，相关则必须保留。'})
    return candidates[:64]


def condition_exemption_matches(exemptions, *, surface, fact_id, qualifier, text) -> bool:
    """Rebind the exemption at use time so a changed draft cannot inherit it."""
    return any(isinstance(row, dict) and row.get('surface') == surface
               and row.get('fact_id') == fact_id and row.get('qualifier') == qualifier
               and row.get('claim_quote') == text and row.get('applicable') is False
               for row in (exemptions or []))
