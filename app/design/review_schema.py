"""Provider-compatible strict wire schema; semantic constraints stay server-side.

OpenAI reference: https://developers.openai.com/api/docs/guides/structured-outputs
Conditional JSON Schema keywords unsupported by Structured Outputs are omitted
only from the provider projection, never from marketing_review's validator.
"""
from __future__ import annotations

from copy import deepcopy
import json
import re

from ..marketing_review import MARKETING_REVIEW_SCHEMA
from ..poster import PosterLayer

UNSUPPORTED_COMPOSITION = {'allOf', 'not', 'dependentRequired', 'dependentSchemas', 'if', 'then', 'else'}
EDIT_FIELDS = ('x', 'y', 'width', 'height', 'z', 'font_size', 'font_weight', 'font_family',
               'letter_spacing', 'line_height', 'color', 'fill', 'align', 'radius')
CATEGORIES = ('composition', 'typography', 'imagery', 'platform', 'specificity')


def _provider_projection(value):
    if isinstance(value, list):
        return [_provider_projection(item) for item in value]
    if not isinstance(value, dict):
        return value
    result = {key: _provider_projection(item) for key, item in value.items()
              if key not in UNSUPPORTED_COMPOSITION | {'default', 'title'}}
    if 'const' in result:
        result['enum'] = [result.pop('const')]
    if result.get('type') == 'object' or 'properties' in result:
        result['type'] = 'object'
        result['additionalProperties'] = False
        result['required'] = list(result.get('properties', {}))
    return result


def visual_review_schema(*, marketing_required: bool) -> dict:
    """One combined visual+marketing root, with every object field required."""
    layer_properties = PosterLayer.model_json_schema()['properties']
    changes = {'type': 'object', 'properties': {
        key: {'anyOf': [deepcopy(layer_properties[key]), {'type': 'null'}]} for key in EDIT_FIELDS}}
    changes['properties']['fill']['description'] = 'Six-digit HEX fill or none for solid stroke-only shapes; preserve intentional open paths.'
    schema = {'type': 'object', 'properties': {
        'review_completed': {'type': 'boolean'},
        'score': {'type': 'number', 'minimum': 0, 'maximum': 100},
        'checks': {'type': 'array', 'minItems': 5, 'maxItems': 5,
            'items': {'type': 'object', 'properties': {
                'category': {'type': 'string', 'enum': list(CATEGORIES)},
                'severity': {'type': 'string', 'enum': ['info', 'warning', 'error']},
                'message': {'type': 'string', 'minLength': 1, 'maxLength': 1800}}}},
        'edits': {'type': 'array', 'maxItems': 40, 'items': {'type': 'object', 'properties': {
            'id': {'type': 'string', 'minLength': 1, 'maxLength': 160}, 'changes': changes}}},
    }}
    if marketing_required:
        marketing = deepcopy(MARKETING_REVIEW_SCHEMA)
        schema['$defs'] = marketing.pop('$defs', {})
        marketing['properties']['phase'] = {'type': 'string', 'enum': ['final']}
        schema['properties']['marketing_review'] = marketing
    return _provider_projection(schema)


def supports_strict_visual_schema(model: str) -> bool:
    """Known model families only; arbitrary compatible endpoints remain explicit."""
    return bool(re.fullmatch(r'gpt-(?:4\.1(?:-(?:mini|nano))?|4o(?:-mini)?|6(?:[.][0-9]+)?(?:-[a-z]+)*)(?:-[0-9]{4}-[0-9]{2}-[0-9]{2})?', str(model).lower()))


def visual_response_format(model: str, *, marketing_required: bool) -> dict:
    if not supports_strict_visual_schema(model):
        return {'type': 'json_object'}
    return {'type': 'json_schema', 'json_schema': {'name': 'commercial_visual_review', 'strict': True,
                                                'schema': visual_review_schema(marketing_required=marketing_required)}}


def decode_nullable_edits(raw):
    """Remove only null edit changes, never missing findings or judgements."""
    if not isinstance(raw, dict):
        return raw
    result = deepcopy(raw)
    for edit in result.get('edits', []) if isinstance(result.get('edits'), list) else []:
        if isinstance(edit, dict) and isinstance(edit.get('changes'), dict):
            edit['changes'] = {key: value for key, value in edit['changes'].items() if value is not None}
    return result


def explicitly_unsupported_schema(status: int, payload: bytes) -> bool:
    """Retry only an explicit 400 format capability rejection, not bad output."""
    if status != 400:
        return False
    try:
        error = json.loads(payload).get('error')
    except (ValueError, TypeError, AttributeError):
        return False
    if not isinstance(error, dict) or not isinstance(error.get('message'), str):
        return False
    message = error['message'].lower()
    format_named = 'json_schema' in message or 'response_format' in message
    unsupported = any(term in message for term in ('not supported', 'unsupported', 'does not support',
                                                  'not support', 'unknown parameter', 'unrecognized parameter'))
    # A malformed schema authored by us must remain observable, not hidden by
    # falling back to prompt-only JSON. This path is capability compatibility.
    invalid_schema = error.get('code') == 'invalid_json_schema' or 'invalid schema' in message
    return format_named and unsupported and not invalid_schema
