import base64
import io
import xml.etree.ElementTree as ET

import pytest
from PIL import Image

from app.models import Asset, ContentBlock, ImagePlacement, RunResult, TaskInput
from app.poster import PosterBrief, render_svg
from app.vector_design import compose_vector_poster


def sample():
    task = TaskInput(product='HUAWEI MateBook 14', audience='大学生', scene='图书馆写论文', platform='poster')
    result = RunResult(title='把灵感，留在课堂', intro='从课程任务出发，选择你的电脑', cta='带上你的课程任务，到店体验',
        blocks=[ContentBlock(text='屏幕表现，现场验证', fact_ids=['f1'], disclosures=['具体配置以实际选购版本为准']),
                ContentBlock(text='跨设备协作，现场体验', fact_ids=['f2'])],
        assets=[Asset(id='real-product', product=task.product, source_id='ima-source', verified=True,
                      url='https://private.invalid/token-do-not-export')],
        images=[ImagePlacement(asset_id='real-product', position='封面', caption='官方产品')])
    return task, result


def test_editorial_spine_is_short_section_title_instead_of_raw_prompt():
    task,result=sample()
    task.scene='给大学生做一张HUAWEI MateBook 14海报主题是图书馆写论文'
    doc=compose_vector_poster(task,result,PosterBrief(direction='editorial'))
    index=next(layer for layer in doc.layers if layer.name=='场景索引')
    assert index.text=='学习日常'
    assert task.scene not in doc.model_dump_json()


def test_internal_purpose_is_not_customer_copy_and_reviewed_source_has_no_draft_notice():
    task, result = sample()
    result.status = 'ready'
    result.intro = ''
    doc = compose_vector_poster(task, result, PosterBrief(purpose='门店引流'))
    public_text = '\n'.join(layer.text for layer in doc.layers)
    assert '门店引流' not in public_text
    assert '设计草稿' not in public_text
    assert next(layer for layer in doc.layers if layer.role == 'watermark').locked


@pytest.mark.parametrize('preset', ['portrait','square','story','landscape','a4'])
@pytest.mark.parametrize('direction', ['minimal','editorial','bold'])
def test_vector_artboards_preserve_complete_content_and_provenance(preset, direction):
    task, result = sample()
    doc = compose_vector_poster(task, result, PosterBrief(size_preset=preset,direction=direction))
    assert not [issue for issue in doc.audit if issue['severity']=='error'], doc.audit
    assert next(layer for layer in doc.layers if layer.role=='product').locked
    assert next(layer for layer in doc.layers if layer.role=='disclosure').text == result.blocks[0].disclosures[0]
    assert next(layer for layer in doc.layers if layer.role=='watermark').locked
    assert 'token-do-not-export' not in doc.model_dump_json()
    assert all(layer.radius == 0 for layer in doc.layers if layer.type=='shape')
    assert not any(layer.role=='generated_background' for layer in doc.layers)
    output = io.BytesIO()
    Image.new('RGB',(60,40),'white').save(output,format='PNG')
    svg = render_svg(doc, {'real-product':output.getvalue()})
    ET.fromstring(svg)
    assert 'data:image/png' in svg and '<text ' in svg


def test_directions_are_different_layouts_not_palette_swaps():
    task,result=sample()
    docs=[compose_vector_poster(task,result,PosterBrief(direction=direction))
          for direction in ['minimal','editorial','bold']]
    positions=[tuple((layer.role,layer.x,layer.y,layer.width,layer.height)
                     for layer in doc.layers if layer.role in ['headline','product','cta']) for doc in docs]
    assert len(set(positions))==3
    assert len({doc.canvas.background for doc in docs})==3


def test_unverified_generated_background_never_enters_vector_layout():
    task,result=sample()
    doc=compose_vector_poster(task,result,PosterBrief(background_mode='generate',background_asset_id='untrusted'))
    assert not any(layer.asset_id=='untrusted' for layer in doc.layers)


def test_full_long_commercial_conditions_are_preserved_and_blocked():
    task,result=sample()
    condition='需满足指定设备、系统版本及应用条件；'*60
    result.blocks[0].disclosures=[condition]
    doc=compose_vector_poster(task,result,PosterBrief())
    assert next(layer for layer in doc.layers if layer.role=='disclosure').text==condition
    assert doc.status=='blocked'
    assert any(issue['category']=='overflow' for issue in doc.audit)


def test_user_headline_and_actual_fact_ids_remain_editable():
    task,result=sample()
    doc=compose_vector_poster(task,result,PosterBrief(headline='为你的论文，留一页'))
    headline=next(layer for layer in doc.layers if layer.role=='headline')
    assert headline.text=='为你的论文，留一页' and not headline.locked
    benefits=[layer for layer in doc.layers if layer.role=='benefit']
    assert [layer.fact_ids for layer in benefits]==[['f1'],['f2']]


def test_missing_exact_product_stays_blocked():
    task,result=sample()
    result.assets[0].product='其他型号'
    doc=compose_vector_poster(task,result,PosterBrief())
    assert doc.status=='blocked'
    assert not [layer for layer in doc.layers if layer.role=='product']
