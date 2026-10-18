"""Images must serve the story, not merely share its product name."""
from app.editorial import bind_legacy_images, choose_preview_images, image_editorial_audit
from app.models import Asset, ContentBlock, EvidenceBundle, ImagePlacement, RunResult, TaskInput
from app.sales import build_sales_plan
from app.workflow import has_product_claim


def test_checking_software_support_does_not_assert_compatibility():
    assert not has_product_claim('把软件与外设列出来，逐项问清楚版本与支持情况。', [])
    assert not has_product_claim('先确认课程软件是否支持。', [])
    assert has_product_claim('这台电脑支持所有课程软件。', [])
    assert has_product_claim('这台电脑配备32GB内存。', [])


def test_laptop_word_is_not_misclassified_as_handwriting():
    from app.editorial import topics
    assert 'pen' not in topics('桌面上的笔记本、平板与手机')


def test_public_disclosure_can_preserve_a_qualifier_but_cannot_add_claims():
    from tests.test_workflow_adversarial import run, draft
    content=draft()
    content['blocks'][0]['text']='内存16GB。'
    content['blocks'][0]['disclosures']=['需兼容设备']
    assert run(content=content).status=='ready'
    content['blocks'][0]['disclosures']=['需兼容设备，保证兼容所有软件']
    assert run(content=content).status=='blocked'


def test_decimal_at_start_of_a_poster_is_not_mistaken_for_list_number():
    from tests.test_workflow_adversarial import evidence, draft, run
    bundle=evidence()
    bundle.facts[0].value='14.2 英寸'
    bundle.facts[0].quote='屏幕14.2 英寸。需兼容设备。'
    bundle.sources[0].excerpt=bundle.facts[0].quote
    content=draft()
    content['blocks'][0]['text']='14.2 英寸屏幕，需兼容设备。'
    assert run(bundle=bundle,content=content,platform='poster').status=='ready'


def test_writer_cannot_borrow_other_fields_from_a_shared_original_chapter():
    from tests.test_workflow_adversarial import evidence
    from app.llm import OpenAICompatibleLLM
    bundle=evidence()
    bundle.facts[0].quote+='其他机型支持999W充电。'
    bundle.sources[0].excerpt=bundle.facts[0].quote
    llm=OpenAICompatibleLLM()
    context=llm._writer_context(bundle,bundle.facts,1)
    assert '999W' not in str(context)
    assert context['facts'][0]['value']=='16GB'
    assert any(s['text']=='需兼容设备' for s in context['evidence_spans'])


def test_similar_feature_name_cannot_be_invented_from_other_ecosystem_features():
    from tests.test_workflow_adversarial import draft, run
    content=draft()
    content['blocks'][0]['text']='内存16GB，需兼容设备，提供多屏协同类功能。'
    result=run(content=content)
    assert result.status=='blocked'
    assert any('多屏协同' in a.message for a in result.audit if a.severity=='error')


def test_unplaced_images_do_not_silently_bind_to_last_paragraph():
    image = ImagePlacement(asset_id='a', caption='素材', position='随便放')
    assert bind_legacy_images([image], 3)[0].block_index is None
    result = RunResult(blocks=[ContentBlock(text='需要核对接口')], images=[image])
    bundle = EvidenceBundle(product='test', assets=[Asset(id='a', product='test', url='', source_id='s')])
    assert any(x.severity == 'error' for x in image_editorial_audit(result, bundle))


def test_same_product_but_irrelevant_photo_is_rejected():
    result = RunResult(blocks=[ContentBlock(text='确认需要哪些接口和投影连接')], images=[
        ImagePlacement(asset_id='a', caption='屏幕', position='第1段后', block_index=0, role='detail', reason='测试')])
    bundle = EvidenceBundle(product='test', assets=[Asset(id='a', product='test', url='', source_id='s',
        title='屏幕显示细节', visual_role='feature_detail')])
    assert any('主题不符' in x.message for x in image_editorial_audit(result, bundle))


def test_no_relevant_detail_is_not_replaced_with_more_color_photos():
    bundle = EvidenceBundle(product='test', assets=[Asset(id=str(i), product='test', url='', source_id='s',
        title='配色外观', visual_role='product_overview') for i in range(6)])
    chosen = choose_preview_images(TaskInput(platform='xiaohongshu'), bundle,
        [ContentBlock(text='需要确认课程软件是否适配')])
    assert len(chosen) == 1 and chosen[0].role == 'cover'


def test_unrelated_image_becomes_explicit_gap_before_reaudit():
    from app.editorial import finalize_editorial_layout
    result = RunResult(blocks=[ContentBlock(text='请确认投影接口')], images=[
        ImagePlacement(asset_id='a',caption='屏幕',position='第1段后',block_index=0,role='detail')])
    bundle = EvidenceBundle(product='test',assets=[Asset(id='a',product='test',url='',source_id='s',
        title='屏幕细节',visual_role='feature_detail')])
    edits=finalize_editorial_layout(TaskInput(platform='xiaohongshu'),result,bundle)
    assert result.images==[] and edits
    assert result.content_plan['image_brief'][0]['status']=='待补素材'


def test_product_photo_does_not_become_fake_customer_photo():
    result = RunResult(images=[ImagePlacement(asset_id='a', caption='客户现场实拍', position='封面', role='cover')])
    bundle = EvidenceBundle(product='test', assets=[Asset(id='a', product='test', url='', source_id='s', title='产品图')])
    assert any(x.severity == 'error' and '未经证实' in x.message for x in image_editorial_audit(result, bundle))


def test_followups_distinguish_purchase_stage_and_never_claim_delivery():
    before = build_sales_plan(TaskInput(stage='consideration'), EvidenceBundle(product='test'), {})
    after = build_sales_plan(TaskInput(stage='onboarding'), EvidenceBundle(product='test'), {})
    assert [x['day'] for x in before['followups']] == [1, 3, 7]
    assert before['followups'][0]['message'] != after['followups'][0]['message']
    assert '未发送' in before['delivery']
    assert all(x['stop_if'] for x in before['followups'])
    assert before['quote']['status'] == 'needs_rules'
