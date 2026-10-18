from app.facts import extract_product_facts


def test_parser_covers_multiple_sections_and_preserves_exact_quotes_and_footnotes():
    text = """# TestBook - 产品事实
## 操作系统
HarmonyOS
## 特色应用
支持跨设备文件共享。
*需支持的设备，不保证所有软件兼容。
## 电池
容量
约70Wh
*典型值，实际使用情况可能不同。
## 接口
USB-C接口
## 尺寸与重量
重量
约1.2kg
## 屏幕
分辨率
2880x1920
亮度
最高500nit
## 参考价格
价格：9999元
"""
    facts = extract_product_facts(text, "TestBook", "s1")
    labels = " ".join(f.label for f in facts)
    assert all(section in labels for section in ["操作系统", "特色应用", "电池", "接口", "尺寸与重量", "屏幕"])
    assert all(f.quote in text and f.value in f.quote for f in facts)
    assert all("价格" not in f.label for f in facts)
    collaboration = next(f for f in facts if "特色应用" in f.label)
    assert "需支持的设备，不保证所有软件兼容。" in collaboration.qualifiers
    battery = next(f for f in facts if "电池" in f.label)
    assert "约" in battery.qualifiers
    assert "典型值，实际使用情况可能不同。" in battery.qualifiers


def test_parser_fact_budget_preserves_section_coverage():
    text = "# TestBook\n## 屏幕\n" + "\n".join(f"屏幕参数{i}：{i}nit" for i in range(20)) + "\n## 操作系统\nHarmonyOS\n## 电池\n容量\n70Wh\n## 接口\nUSB-C\n"
    facts = extract_product_facts(text, "TestBook", "s1", max_facts=4)
    assert len(facts) == 4
    assert all(any(section in f.label for f in facts) for section in ["操作系统", "电池", "接口", "屏幕"])


def test_parser_website_chrome_is_not_product_fact():
    text = "# TestBook\n## 内存\n16GB\n即将离开华为网站\n购物车：999\n继续访问\n"
    facts = extract_product_facts(text, "TestBook", "s1")
    assert facts and all("999" not in f.value for f in facts)


def test_cpu_gpu_and_audio_field_names_are_not_product_values():
    text = '# TestBook\n## 处理器\nCPU\n麒麟 X90\nGPU\n测试集成图形\n## 音频\n扬声器\n扬声器 × 2\n麦克风\n麦克风 × 2\n'
    facts = extract_product_facts(text, 'TestBook', 's1')
    cpu = [f for f in facts if f.label.endswith(' / CPU')]
    assert len(cpu) == 1 and cpu[0].value == '麒麟 X90'
    assert not any(f.value in {'CPU','GPU','扬声器','麦克风'} for f in facts)
    assert any(f.label.endswith(' / GPU') and f.value == '测试集成图形' for f in facts)
    assert all(f.quote in text and f.value in f.quote for f in facts)


def test_multiline_configuration_alternatives_remain_one_verbatim_value():
    text = '# TestBook\n## 操作系统\nWindows 11 家庭版\n\nLinux 版\n## 尺寸与重量\n重量\n约 1.33 kg（圆键盘）\n\n约 1.32 kg（方键盘）\n'
    facts = extract_product_facts(text, 'TestBook', 's1')
    os = [f for f in facts if f.label == '操作系统']
    weight = [f for f in facts if f.label.endswith(' / 重量')]
    assert len(os) == len(weight) == 1
    assert 'Windows' in os[0].value and 'Linux' in os[0].value
    assert '圆键盘' in weight[0].value and '方键盘' in weight[0].value
    assert all(f.value in text for f in facts)
