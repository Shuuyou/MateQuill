import hashlib
import io

import pytest
from PIL import Image, ImageDraw

from app.design.asset_preparation import (
    PreparationUnsupported, capabilities, grabcut_mask, prepare_product_asset,
)


def png(image):
    out = io.BytesIO()
    image.save(out, format='PNG')
    return out.getvalue()


def source(alpha=True):
    image = Image.new('RGBA', (120, 100), (240, 240, 240, 0 if alpha else 255))
    draw = ImageDraw.Draw(image)
    draw.rounded_rectangle((35, 10, 84, 89), radius=8, fill=(44, 120, 133, 255))
    draw.ellipse((42, 15, 70, 43), fill=(10, 10, 10, 255))
    draw.text((44, 60), 'H', fill=(255, 255, 255, 255))
    return image


def test_real_alpha_has_exact_rgb_cropped_pixels_and_reproducible_hashes():
    original = source()
    raw = png(original)
    result = prepare_product_asset(raw, asset_kind='packshot', expected_sha256=hashlib.sha256(raw).hexdigest())
    actual = Image.open(io.BytesIO(result.derived_bytes))
    assert actual.convert('RGB').tobytes() == original.crop((35, 10, 85, 90)).convert('RGB').tobytes()
    assert result.original_bytes == raw
    assert result.manifest['crop_box'] == [35, 10, 85, 90]
    assert result.manifest['effective_height'] == 80
    assert result.manifest['derived_sha256'] == hashlib.sha256(result.derived_bytes).hexdigest()
    assert not result.manifest['pixel_quality_certified']
    assert prepare_product_asset(raw, asset_kind='packshot').derived_bytes == result.derived_bytes


@pytest.mark.parametrize('kind', ['official_kv', 'store_photo', 'unknown'])
def test_transparency_alone_does_not_upgrade_whole_kv_or_photo(kind):
    with pytest.raises(PreparationUnsupported, match='non_packshot'):
        prepare_product_asset(png(source()), asset_kind=kind)


def test_opaque_and_hash_mismatch_are_not_silently_processed():
    with pytest.raises(PreparationUnsupported, match='opaque_packshot'):
        prepare_product_asset(png(source(False)), asset_kind='packshot')
    with pytest.raises(PreparationUnsupported, match='hash_mismatch'):
        prepare_product_asset(png(source()), asset_kind='packshot', expected_sha256='a'*64)


def test_supplied_mask_cannot_repaint_product_and_preserves_source_class():
    original = source(False)
    mask = source().getchannel('A')
    result = prepare_product_asset(png(original), asset_kind='official_kv', mask_bytes=png(mask),
                                   mask_provenance={'backend': 'external_segmentation', 'model_sha256': 'abc'})
    derived = Image.open(io.BytesIO(result.derived_bytes))
    assert derived.convert('RGB').tobytes() == original.crop((35, 10, 85, 90)).convert('RGB').tobytes()
    assert result.manifest['source_kind'] == 'official_kv'
    assert result.manifest['requires_pixel_review']
    assert Image.open(io.BytesIO(result.mask_bytes)).size == original.size
    asset = result.as_asset({'source_id': 'authorized-source', 'sha256': 'old'})
    assert asset['source_id'] == 'authorized-source'
    assert asset['sha256'] == result.manifest['derived_sha256']
    assert asset['pixel_budget']['effective_width'] == 50


@pytest.mark.parametrize('mask,reason', [
    (Image.new('L', (10, 10), 255), 'dimensions'),
    (Image.new('L', (120, 100), 255), 'does_not_separate'),
    (Image.new('L', (120, 100), 0), 'does_not_separate'),
    (Image.new('RGB', (120, 100), (255, 0, 0)), 'grayscale'),
])
def test_bad_masks_fail_explicitly(mask, reason):
    with pytest.raises(PreparationUnsupported, match=reason):
        prepare_product_asset(png(source(False)), asset_kind='official_kv', mask_bytes=png(mask),
                              mask_provenance={'backend': 'candidate'})


def test_mask_provenance_required():
    with pytest.raises(PreparationUnsupported, match='provenance'):
        prepare_product_asset(png(source(False)), asset_kind='packshot', mask_bytes=png(source().getchannel('A')))


@pytest.mark.skipif(not capabilities()['grabcut_available'], reason='optional OpenCV unavailable')
def test_bbox_segmentation_is_only_a_candidate_and_archives_exact_mask():
    raw = png(source(False))
    mask, provenance = grabcut_mask(raw, (25, 5, 95, 95))
    result = prepare_product_asset(raw, asset_kind='packshot', mask_bytes=mask, mask_provenance=provenance)
    assert result.manifest['requires_pixel_review']
    assert result.manifest['operation']['subject_box'] == [25, 5, 95, 95]
    assert Image.open(io.BytesIO(result.derived_bytes)).getchannel('A').getbbox()
    with pytest.raises(PreparationUnsupported, match='background_margin'):
        grabcut_mask(raw, (0, 0, 120, 100))


def test_existing_alpha_is_combined_not_replaced_by_mask():
    original = source()
    # A permissive opaque mask must never restore original transparent corners.
    result = prepare_product_asset(png(original), asset_kind='official_kv',
        mask_bytes=png(Image.new('L', original.size, 255)), mask_provenance={'backend': 'mask'})
    assert result.manifest['subject_bounds'] == [35, 10, 85, 90]
    assert Image.open(io.BytesIO(result.derived_bytes)).getpixel((0, 0))[3] == 0


def test_partial_official_view_records_cut_edge_without_reconstructing_body():
    image = source()
    raw = png(image.crop((0, 0, 120, 70)))
    result = prepare_product_asset(raw, asset_kind='packshot')
    assert result.manifest['subject_touches_source_edges'] == ['bottom']
    assert result.manifest['output_size'] == [50, 60]
    assert Image.open(io.BytesIO(result.derived_bytes)).convert('RGB').tobytes() == image.crop((35, 10, 85, 70)).convert('RGB').tobytes()
