from scripts.collect_official_assets import comparison_subject_candidates, prioritized_candidates


def markup(label='HUAWEI Mate 80', path='/exact.png'):
    return f'''<div class="accessory-compare-row">
    <div class="accessory-compare-header-product-name">{label}</div>
    <div class="accessory-compare-header-product-name">Other Pro</div>
    <div class="accessory-compare-product-switch-item"><picture>
    <source type="image/png" data-srcset="{path} 1.5x"><img data-src="/thumb.png">
    </picture></div><div class="accessory-compare-product-switch-item"><img data-src="/wrong.png"></div></div>'''


def test_subject_is_bound_to_labeled_first_column_and_actual_picture_url():
    result = comparison_subject_candidates(markup(), 'https://consumer.huawei.com/cn/phones/mate80/', 'HUAWEI Mate 80')
    assert len(result) == 1
    assert result[0]['image_url'] == 'https://consumer.huawei.com/exact.png'
    assert result[0]['source_binding']['product'] == 'HUAWEI Mate 80'
    assert result[0]['asset_kind'] == 'packshot'


def test_wrong_model_external_host_and_unlabeled_images_are_not_subject_proof():
    page = 'https://consumer.huawei.com/cn/phones/mate80/'
    assert comparison_subject_candidates(markup('HUAWEI Mate 80 Pro'), page, 'HUAWEI Mate 80') == []
    # No fallback to another image when every image in the column is external.
    html = markup(path='https://untrusted.example/image.png').replace('/thumb.png', 'https://untrusted.example/thumb.png')
    assert comparison_subject_candidates(html, page, 'HUAWEI Mate 80') == []
    assert comparison_subject_candidates('<img src="/mate80.png">', page, 'HUAWEI Mate 80') == []


def test_collection_reserves_kv_and_subject_before_generic_banners():
    rows = [{'visual_role': 'product', 'score': 1000},
            {'visual_role': 'campaign_key_visual', 'score': 1},
            {'visual_role': 'product', 'score': 10, 'asset_kind': 'packshot'}]
    assert prioritized_candidates(rows)[:2] == [rows[1], rows[2]]
