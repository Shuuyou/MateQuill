from pathlib import Path
import json

from app.facts import extract_product_facts


def phone_source():
    catalog = json.loads(Path('data/ecosystem_products.json').read_text(encoding='utf8'))
    return next(p for p in catalog['products'] if '支持 5.5 倍光学变焦' in p.get('text', ''))


def test_real_composite_camera_field_values_remain_exact_and_local_notes_stay_local():
    source = phone_source()
    facts = extract_product_facts(source['text'], 'AnyCameraProduct', 'official-snapshot', max_facts=100)
    zoom = next(f for f in facts if f.label.endswith(' / 后置摄像头变焦模式'))
    assert zoom.value == '支持 5.5 倍光学变焦（5.5 倍变焦为近似值，镜头焦段分别为 24 mm，13 mm，125 mm）、50 倍数字变焦'
    assert zoom.value in source['text'] and zoom.quote in source['text']
    assert not any('HOTA' in q or q == '最大' for q in zoom.qualifiers)
    rear_photo = next(f for f in facts if f.label.endswith(' / 后置摄像头照片分辨率'))
    rear_video = next(f for f in facts if f.label.endswith(' / 后置摄像头摄像分辨率'))
    features = next(f for f in facts if f.label.endswith(' / 后置摄像头拍摄功能'))
    assert rear_photo.value == '最大可支持 8192 × 6144 像素'
    assert rear_photo.qualifiers == ['不同拍照模式的照片像素可能有差异，请以实际为准。']
    assert rear_video.qualifiers == ['不同拍摄模式的视频像素可能有差异，请以实际为准。']
    assert features.qualifiers == ['需 HOTA 升级支持。']
    assert '电影效果*' in features.value
    assert any(f.label.endswith(' / 后置闪光灯') and f.value == 'LED 闪光灯' for f in facts)
    assert all(f.value in f.quote and f.quote in source['text'] for f in facts)
    assert not any(q in {'约','典型值','额定容量','最高','最大','仅'} for f in facts for q in f.qualifiers)


def test_real_wireless_charge_keeps_complete_purchase_kit_and_power_conditions():
    source = phone_source()
    facts = extract_product_facts(source['text'], 'AnyDevice', 'source', max_facts=100)
    charging = next(f for f in facts if f.label == '充电')
    assert '50 W' in charging.value
    assert any('需单独购买' in q and '特定 66 W 及以上' in q for q in charging.qualifiers)
    assert any('实际充电功率会随不同场景智能变化' in q for q in charging.qualifiers)
    assert all(q in source['text'] for q in charging.qualifiers)


def test_compound_rule_is_not_model_specific_and_keeps_unknown_conditions():
    text = """## 测试相机
测试相机
镜头 A
*该镜头仅在指定模式下启用。
测试相机变焦模式
约 3 倍，最高 12 倍
*需搭配专用附件。
测试相机摄像分辨率
最大 4K
*仅限特定帧率。
"""
    facts = extract_product_facts(text, 'OtherBrand', 's', 100)
    zoom = next(f for f in facts if f.label.endswith(' / 测试相机变焦模式'))
    assert zoom.value == '约 3 倍，最高 12 倍'
    assert zoom.qualifiers == ['需搭配专用附件。']
    assert any('该镜头仅在指定模式下启用。' in f.qualifiers for f in facts)
    assert all(f.quote in text and f.value in f.quote for f in facts)


def test_unrecognized_preamble_falls_back_without_losing_global_conditions():
    text = '## 后置摄像头\n此章所有模式需附件。\n后置摄像头\n镜头A\n后置摄像头变焦模式\n约3倍\n*需升级。\n'
    facts = extract_product_facts(text, 'Other', 's', 100)
    assert any('此章所有模式需附件。' in f.value and '需升级。' in f.qualifiers for f in facts)
