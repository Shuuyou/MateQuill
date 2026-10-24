"""Reproducible alpha/crop preparation of caller-authorized product pixels.

No URL fetching, model download, recoloring, inpainting or perspective changes.
The caller owns authorization and supplies the source kind and optional mask.
Segmentation candidates require pixel review; provenance is not a quality pass.
"""
from __future__ import annotations

import base64
import hashlib
import importlib.util
import io
import threading
from dataclasses import dataclass

from PIL import Image, ImageChops

POLICY = 'product-pixel-preparation.v1'
MAX_PIXELS = 24_000_000
_GRABCUT_LOCK = threading.Lock()


class PreparationUnsupported(ValueError):
    """Source cannot truthfully be advertised as a separated product."""


@dataclass(frozen=True)
class PreparedAsset:
    original_bytes: bytes
    derived_bytes: bytes
    mask_bytes: bytes
    manifest: dict

    def as_asset(self, source: dict) -> dict:
        """Preserve authorized identity while binding the actual derived bytes."""
        return {**source, 'data_url': 'data:image/png;base64,' + base64.b64encode(self.derived_bytes).decode(),
                'sha256': self.manifest['derived_sha256'], 'mime': 'image/png',
                'width': self.manifest['output_size'][0], 'height': self.manifest['output_size'][1],
                'effective_width': self.manifest['effective_width'],
                'effective_height': self.manifest['effective_height'],
                'pixel_budget': self.manifest['pixel_budget'],
                'visual_role': 'product_cutout', 'preparation': self.manifest}


def _sha(raw):
    return hashlib.sha256(raw).hexdigest()


def _png(image):
    out = io.BytesIO()
    image.save(out, format='PNG', optimize=False)
    return out.getvalue()


def _read(raw):
    if not isinstance(raw, bytes) or not raw or len(raw) > 32_000_000:
        raise PreparationUnsupported('invalid_source_bytes')
    with Image.open(io.BytesIO(raw)) as image:
        if image.width * image.height > MAX_PIXELS:
            raise PreparationUnsupported('source_pixel_budget_exceeded')
        if getattr(image, 'n_frames', 1) != 1:
            raise PreparationUnsupported('animated_asset_unsupported')
        # Do not silently rotate/resample pixels. EXIF transforms belong to a
        # separate explicitly recorded source normalization operation.
        if image.getexif().get(274, 1) != 1:
            raise PreparationUnsupported('exif_orientation_requires_normalization')
        return image.convert('RGBA')


def capabilities() -> dict:
    return {'policy': POLICY, 'existing_alpha': True, 'supplied_alpha_mask': True,
            'grabcut_available': importlib.util.find_spec('cv2') is not None,
            'grabcut_scope': 'bbox-initialized candidate; actual-pixel review required',
            'automatic_neural_segmentation': False, 'downloads_models': False,
            'model_license': None,
            'references': ['https://docs.opencv.org/4.x/d8/d83/tutorial_py_grabcut.html'],
            'unsupported': ['opaque_asset_without_mask_or_subject_bbox', 'generative_product_redrawing']}


def grabcut_mask(raw: bytes, subject_box: tuple[int, int, int, int], *, iterations: int = 5) -> tuple[bytes, dict]:
    """Agent-selected source-pixel bbox, not a new salesperson input form.

    Returns a candidate, never a certified contour. Callers must inspect the
    derived image, including camera circles, antenna edges and word marks.
    """
    import numpy as np
    import cv2

    image = _read(raw)
    if not 1 <= iterations <= 8:
        raise ValueError('invalid_segmentation_iterations')
    if (len(subject_box) != 4 or any(type(v) is not int for v in subject_box)):
        raise ValueError('invalid_subject_box')
    x0, y0, x1, y1 = subject_box
    if not (0 <= x0 < x1 <= image.width and 0 <= y0 < y1 <= image.height):
        raise PreparationUnsupported('invalid_subject_box')
    if (x1-x0)*(y1-y0) >= image.width*image.height*.98:
        raise PreparationUnsupported('grabcut_requires_background_margin')
    rgb = np.asarray(image.convert('RGB'))
    mask = np.zeros((image.height, image.width), np.uint8)
    mask[y0:y1, x0:x1] = cv2.GC_PR_FGD
    # Local RNG changes are serialized by OpenCV's implementation; do not
    # promise cross-version byte identity. The exact emitted mask is archived.
    with _GRABCUT_LOCK:
        cv2.setRNGSeed(0)
        cv2.grabCut(rgb, mask, (x0, y0, x1-x0, y1-y0),
                    np.zeros((1, 65), np.float64), np.zeros((1, 65), np.float64),
                    iterations, cv2.GC_INIT_WITH_MASK)
    alpha = Image.fromarray(np.where((mask == cv2.GC_FGD) | (mask == cv2.GC_PR_FGD), 255, 0).astype('uint8'))
    return _png(alpha), {'backend': 'opencv_grabcut', 'backend_version': cv2.__version__,
                         'subject_box': list(subject_box), 'iterations': iterations,
                         'seed': 0, 'requires_pixel_review': True}


def prepare_product_asset(raw: bytes, *, asset_kind: str, expected_sha256: str | None = None,
                          mask_bytes: bytes | None = None, mask_provenance: dict | None = None,
                          crop: bool = True) -> PreparedAsset:
    """Produce a source-RGB cutout with a full-resolution archived alpha mask.

    Transparent packshots are accepted directly. Opaque packshots and complete
    official KVs require a supplied segmentation mask with backend provenance.
    KV classification is retained, never upgraded into an original packshot.
    """
    if asset_kind not in {'packshot', 'official_kv', 'store_photo', 'unknown'}:
        raise ValueError('invalid_asset_kind')
    if expected_sha256 and _sha(raw) != expected_sha256:
        raise PreparationUnsupported('source_hash_mismatch')
    image = _read(raw)
    original_alpha = image.getchannel('A')
    if mask_bytes is None:
        if asset_kind != 'packshot':
            raise PreparationUnsupported('non_packshot_requires_subject_mask')
        if original_alpha.getextrema()[0] == 255:
            raise PreparationUnsupported('opaque_packshot_requires_subject_mask')
        alpha, operation = original_alpha, {'backend': 'source_alpha'}
        requires_review = False
    else:
        if not isinstance(mask_provenance, dict) or not mask_provenance.get('backend'):
            raise PreparationUnsupported('mask_provenance_required')
        mask = _read(mask_bytes)
        if mask.size != image.size:
            raise PreparationUnsupported('mask_dimensions_mismatch')
        # Masks are grayscale coverage, not colored overlays or replacement RGB.
        r, g, b, _ = mask.split()
        if ImageChops.difference(r, g).getbbox() or ImageChops.difference(r, b).getbbox():
            raise PreparationUnsupported('mask_must_be_grayscale')
        alpha = ImageChops.multiply(original_alpha, r)
        operation = {key: mask_provenance[key] for key in (
            'backend', 'backend_version', 'model_id', 'model_sha256', 'license',
            'subject_box', 'iterations', 'seed') if key in mask_provenance}
        requires_review = True
    bounds = alpha.getbbox()
    if not bounds or alpha.getextrema()[0] == 255:
        raise PreparationUnsupported('mask_does_not_separate_subject')
    coverage = alpha.histogram()
    occupied = sum(coverage[1:])
    if occupied < 64 or occupied / (image.width * image.height) > .985:
        raise PreparationUnsupported('implausible_subject_coverage')
    full_mask = _png(alpha)
    separated = image.copy()
    separated.putalpha(alpha)
    crop_box = bounds if crop else (0, 0, image.width, image.height)
    separated = separated.crop(crop_box)
    derived = _png(separated)
    effective_width, effective_height = bounds[2]-bounds[0], bounds[3]-bounds[1]
    manifest = {'policy': POLICY, 'source_sha256': _sha(raw), 'derived_sha256': _sha(derived),
        'mask_sha256': _sha(full_mask), 'source_kind': asset_kind,
        'source_size': list(image.size), 'output_size': list(separated.size),
        'crop_box': list(crop_box), 'subject_bounds': list(bounds),
        'subject_touches_source_edges': [edge for edge, value in (
            ('left', bounds[0] == 0), ('top', bounds[1] == 0),
            ('right', bounds[2] == image.width), ('bottom', bounds[3] == image.height)) if value],
        'operation': operation, 'rgb_policy': 'exact_source_RGB_at_crop_coordinates',
        'geometry_policy': 'crop_only_no_perspective_or_reconstruction',
        'requires_pixel_review': requires_review, 'pixel_quality_certified': False,
        'foreground_fraction': occupied / (image.width * image.height),
        'effective_width': effective_width, 'effective_height': effective_height,
        'pixel_budget': {'canvas_width': separated.width, 'canvas_height': separated.height,
                         'effective_width': effective_width, 'effective_height': effective_height},
        'scope': 'Separated source pixels; not proof of product completeness, source rights or a photographed event.'}
    return PreparedAsset(raw, derived, full_mask, manifest)
