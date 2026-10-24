"""A text repair must not accidentally discard source-bound publication media."""
import asyncio

from app.llm import OpenAICompatibleLLM
from app.models import EvidenceBundle, ImagePlacement, RunResult, TaskInput


def test_text_repair_preserves_existing_images_when_model_omits_them():
    model = OpenAICompatibleLLM(api_key='test-unused')
    image = ImagePlacement(asset_id='official-phone', caption='官方产品图', position='cover')
    parent = RunResult(images=[image])
    async def repaired(*a, **k):
        return {'title': '修正后的场景', 'images': []}
    model.generate = repaired
    value = asyncio.run(model.repair(TaskInput(), EvidenceBundle(product='手机'), {}, parent))
    assert value['images'] == [image.model_dump()]
    assert parent.images == [image]


def test_text_repair_respects_explicit_replacement_media():
    model = OpenAICompatibleLLM(api_key='test-unused')
    replacement = {'asset_id': 'another-official-image', 'caption': '官方图', 'position': 'cover'}
    async def repaired(*a, **k):
        return {'images': [replacement]}
    model.generate = repaired
    parent = RunResult(images=[ImagePlacement(asset_id='old', caption='旧图', position='cover')])
    value = asyncio.run(model.repair(TaskInput(), EvidenceBundle(product='手机'), {}, parent))
    assert value['images'] == [replacement]
