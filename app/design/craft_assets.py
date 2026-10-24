"""Recover immutable native inputs from the authorized parent design.

Callers must authorize the parent before invoking these helpers. This is local
document reuse, never a new source selection or a generated-image request.
"""
from __future__ import annotations

from copy import deepcopy

from ..poster import PosterDocument
from .source_integrity import RENDER_POLICY, native_source_proof


def collect_parent_assets(parent):
    if parent.get('render_policy') != RENDER_POLICY or not parent.get('artboards'):
        raise ValueError('craft_edit_requires_native_parent')
    assets = {}
    for board in parent['artboards']:
        doc = PosterDocument.model_validate(board['document'])
        native_source_proof(doc, doc.assets)
        for layer in doc.layers:
            if layer.type != 'image':
                continue
            value = doc.assets[layer.asset_id]
            if layer.asset_id in assets and assets[layer.asset_id] != value:
                # Attachment bookkeeping may differ per page, image identity may not.
                without_attachment = lambda item: {k: v for k, v in item.items() if k != 'attachment_adjustments'}
                if without_attachment(assets[layer.asset_id]) != without_attachment(value):
                    raise ValueError('craft_edit_parent_asset_collision')
            assets[layer.asset_id] = deepcopy(value)
    return assets


def parent_board(parent, board):
    matches = [row for row in parent['artboards']
               if row.get('page_id') == board.page_id and row.get('concept_id') == board.concept_id]
    if len(matches) != 1:
        raise ValueError('craft_edit_parent_page_unavailable')
    return matches[0]


def preserve_parent_document(parent, board, baseline, request):
    """Keep every actual parent layer except controls in the selected region.

    A final-review adjustment may exist only in the saved document, not its
    original plan. Recompiling the whole plan would silently undo that work.
    Baseline compilation provides stable region bindings for older native files.
    """
    original = PosterDocument.model_validate(parent_board(parent, board)['document'])
    compiled_typography = deepcopy(board.typography)
    board.typography = deepcopy(parent_board(parent,board).get('typography', {}))
    if board.page_id != request.page_id:
        return original
    def binding(document, region_id):
        if region_id not in document.region_bindings:
            raise ValueError('craft_edit_parent_region_unavailable')
        return document.region_bindings[region_id]
    old_id = binding(original if original.region_bindings else baseline.document, request.region_id)
    new_id = binding(board.document, request.region_id)
    actual = next((item for item in original.layers if item.id == old_id), None)
    compiled = next((item for item in board.document.layers if item.id == new_id), None)
    if (actual is None or compiled is None or actual.type != compiled.type
            or actual.role != compiled.role or actual.asset_id != compiled.asset_id
            or ''.join(actual.text.split()) != ''.join(compiled.text.split())):
        raise ValueError('craft_edit_parent_region_changed')
    renamed = {'font_scale':'font_size', 'font_role':'font_family'}
    fields = {renamed.get(key, key) for key in request.values} - {'overlap_intent', 'font_fit'}
    if 'font_fit' in request.values and actual.type == 'text':
        fields.update({'font_size','text'})
    if actual.type == 'text' and fields & {'font_size', 'font_family', 'font_weight',
                                          'letter_spacing', 'line_height', 'width', 'height'}:
        # Exact approved characters may reflow and fit within this one region.
        fields.update({'font_size', 'text'})
    replacement = actual.model_copy(update={key:deepcopy(getattr(compiled,key)) for key in fields}, deep=True)
    original.layers = [replacement if item.id == old_id else item for item in original.layers]
    # Bind to stable actual layer IDs for subsequent edits, without modifying
    # unrelated historical diagnostics or reinterpreting their source content.
    original.region_bindings = deepcopy(original.region_bindings or baseline.document.region_bindings)
    if new_id in compiled_typography:
        board.typography[old_id] = compiled_typography[new_id]
    return original


def restore_parent_elements(parent, board):
    from .elements import attach_design_elements
    original = PosterDocument.model_validate(parent_board(parent, board)['document'])
    elements = [layer for layer in original.layers if layer.role == 'design_element']
    if len(elements) > 1:
        raise ValueError('craft_edit_parent_plate_unsupported')
    if not elements:
        return board.document
    layer = elements[0]
    metadata = deepcopy(original.assets[layer.asset_id])
    current = [item for item in board.document.layers if item.role == 'design_element']
    if (len(current) == 1 and current[0] == layer
            and board.document.assets.get(layer.asset_id) == metadata):
        return board.document
    # Attach with the same undercoat handling, then restore actual plate position.
    doc = attach_design_elements(board.document, metadata)
    doc.layers = [deepcopy(layer) if item.role == 'design_element' else item for item in doc.layers]
    doc.assets[layer.asset_id] = metadata
    return doc
