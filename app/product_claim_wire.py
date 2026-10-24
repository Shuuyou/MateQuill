"""Grouped model protocol; decode only structure, leaving judgments untouched.

Callers supply the exact public-surface mapping and the captured evidence IDs.
This codec does not select surfaces or make review judgments; app.llm owns the
active review loop and supplies the authorized inputs.
"""
from __future__ import annotations

from collections.abc import Iterable, Mapping
from copy import deepcopy

from pydantic import ValidationError

from .product_claim_review import PRODUCT_CLAIM_SCHEMA, ProductClaimReview


def product_claim_wire_schema(public_surfaces: Mapping[str, str], evidence_ids: Iterable[str]) -> dict:
    """Build an exact-key grouped schema with server-owned quote text omitted."""
    definitions = deepcopy(PRODUCT_CLAIM_SCHEMA['$defs'])
    claim = definitions['ProductClaim']
    for field in ('surface', 'quote'):
        claim['properties'].pop(field)
        claim['required'].remove(field)
    span = definitions['EvidenceQuote']
    span['properties'].pop('quote')
    span['required'].remove('quote')
    identifiers = list(dict.fromkeys(evidence_ids))
    claim['properties']['evidence_ids']['items']['enum'] = identifiers
    span['properties']['evidence_id']['enum'] = identifiers
    groups = {key: {'type': 'array', 'items': {'$ref': '#/$defs/ProductClaim'},
                    'maxItems': 64} for key in public_surfaces}
    return {'type': 'object', 'additionalProperties': False,
            'properties': {'surfaces': {'type': 'object', 'additionalProperties': False,
                                        'properties': groups, 'required': list(groups)}},
            'required': ['surfaces'], '$defs': definitions}


def decode_product_claim_wire(raw: object, public_surfaces: Mapping[str, str],
                              evidence_ids: Iterable[str]) -> dict:
    """Return legacy review input with derived coverage and empty quote slots.

    Pass the result to validate_product_claim_review for semantic/evidence checks
    and immutable text filling. Errors contain fixed safe codes, never payloads.
    """
    if not isinstance(raw, dict) or set(raw) != {'surfaces'}:
        raise ValueError('product_claim_wire_root_invalid')
    groups = raw['surfaces']
    if not isinstance(groups, dict):
        raise ValueError('product_claim_wire_groups_invalid')
    if set(groups) != set(public_surfaces):
        raise ValueError('product_claim_wire_surface_mismatch')
    allowed_evidence = set(evidence_ids)
    claims = []
    coverage = {}
    expected_fields = set(PRODUCT_CLAIM_SCHEMA['$defs']['ProductClaim']['required']) - {'surface', 'quote'}
    for surface in public_surfaces:
        rows = groups[surface]
        if not isinstance(rows, list):
            raise ValueError('product_claim_wire_claims_invalid')
        coverage[surface] = 'claims_listed' if rows else 'no_product_claim'
        for row in rows:
            if not isinstance(row, dict) or set(row) != expected_fields:
                raise ValueError('product_claim_wire_claim_invalid')
            spans = row['evidence_quotes']
            if not isinstance(spans, list) or any(
                    not isinstance(span, dict) or set(span) != {'evidence_id'} for span in spans):
                raise ValueError('product_claim_wire_evidence_quote_invalid')
            claim = deepcopy(row)
            claim.update(surface=surface, quote='')
            claim['evidence_quotes'] = [dict(span, quote='') for span in spans]
            claims.append(claim)
    try:
        normalized = ProductClaimReview.model_validate({'claims': claims, 'coverage': coverage}).model_dump(mode='json')
    except ValidationError:
        raise ValueError('product_claim_wire_value_invalid') from None
    for claim in normalized['claims']:
        references = claim['evidence_ids'] + [span['evidence_id'] for span in claim['evidence_quotes']]
        if any(identifier not in allowed_evidence for identifier in references):
            raise ValueError('product_claim_wire_unknown_evidence')
    return normalized
