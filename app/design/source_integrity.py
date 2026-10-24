"""Bind native image layers to authorized bytes and record display transforms.

This proves composition provenance, not the truth of events inside a photo or
the legal right to publish it. The service supplies its authorized asset map.
"""
from __future__ import annotations

import base64
import hashlib

from ..poster import PosterDocument

RENDER_POLICY = 'asset_native.v3'


def native_source_proof(document, authorized_assets: dict) -> dict:
    doc = document if isinstance(document, PosterDocument) else PosterDocument.model_validate(document)
    placements = []
    design_elements = []
    for layer in doc.layers:
        if layer.type != 'image':
            continue
        if layer.role not in {'product', 'photograph', 'design_element'} or not layer.locked:
            raise ValueError('native_design_requires_authorized_image_layers')
        original = authorized_assets.get(layer.asset_id)
        actual = doc.assets.get(layer.asset_id)
        if not isinstance(original, dict) or not isinstance(actual, dict):
            raise ValueError('native_design_unknown_asset')
        data = original.get('data_url')
        if (not isinstance(data, str) or not data.startswith('data:image/')
                or actual.get('data_url') != data or len(data) > 32_000_100):
            raise ValueError('native_design_source_bytes_changed')
        try:
            payload = base64.b64decode(data.split(',', 1)[1], validate=True)
        except (ValueError, IndexError):
            raise ValueError('native_design_invalid_source_bytes') from None
        digest = hashlib.sha256(payload).hexdigest()
        if (original.get('sha256') != digest or actual.get('sha256') != digest
                or actual.get('source_id') != original.get('source_id')):
            raise ValueError('native_design_source_hash_mismatch')
        if layer.role == 'design_element':
            if (original.get('provenance') != 'ai_generated_design_elements'
                    or actual.get('provenance') != original.get('provenance')
                    or not original.get('model')):
                raise ValueError('native_design_unbound_design_element')
            design_elements.append({'layer_id': layer.id, 'asset_id': layer.asset_id,
                'sha256': digest, 'model': original['model'],
                'request_id': original.get('request_id', ''),
                'purpose': 'generated_abstract_design_only_not_product_or_scene_evidence'})
            continue
        preparation = original.get('preparation') or {}
        if preparation:
            if (actual.get('preparation') != preparation
                    or preparation.get('source_sha256') != original.get('catalog_sha256')
                    or preparation.get('derived_sha256') != digest
                    or preparation.get('rgb_policy') != 'exact_source_RGB_at_crop_coordinates'):
                raise ValueError('native_design_preparation_binding_mismatch')
        placements.append({'layer_id': layer.id, 'asset_id': layer.asset_id,
            'source_id': original.get('source_id', ''),
            'source_sha256': preparation.get('source_sha256') or digest,
            'render_asset_sha256': digest, 'preparation': preparation,
            'role': layer.role, 'visible': layer.visible,
            'display_box': {'x': layer.x, 'y': layer.y, 'width': layer.width, 'height': layer.height},
            'fit': 'slice' if layer.role == 'photograph' else 'contain',
            'opacity': layer.opacity, 'corner_radius': layer.radius,
            'rotation': layer.rotation,
            'contact_shadow': layer.contact_shadow.model_dump() if layer.contact_shadow is not None else None,
            'shadow': {key: getattr(layer, 'shadow_' + key) for key in ('color', 'opacity', 'blur', 'dx', 'dy')}})
    return {'render_policy': RENDER_POLICY,
        'source_bytes_unchanged': all(p['source_sha256'] == p['render_asset_sha256'] for p in placements),
        'source_rgb_preserved': True,
        'design_elements': design_elements,
        'display_transforms_recorded': True, 'placements': placements,
        'scope': 'Original source RGB with recorded alpha/crop preparation and display transforms; no regenerated product structure or event certification.'}
