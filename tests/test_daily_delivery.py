import asyncio

from app.daily_delivery import daily_content_delivery, daily_delivery_record
from app.models import TaskInput
from app.workflow import connector_platform_rule

POLICY = {'source': 'daily_store', 'content_delivery': 'text_with_optional_existing_images'}


def test_daily_text_policy_only_changes_moments_and_does_not_leak():
    moments = TaskInput(platform='moments')
    original = connector_platform_rule(moments)
    assert original['min_images'] >= 1
    with daily_content_delivery(POLICY):
        assert connector_platform_rule(moments)['min_images'] == 0
        assert connector_platform_rule(TaskInput(platform='xiaohongshu'))['min_images'] >= 1
        assert daily_delivery_record('moments', 0)['delivered'] == 'text_only'
        assert daily_delivery_record('moments', 1)['delivered'] == 'text_with_existing_images'
    assert connector_platform_rule(moments) == original
    assert daily_delivery_record('moments', 0) is None


def test_task_payload_cannot_turn_on_server_policy():
    task = TaskInput(platform='moments', content_delivery='text_with_optional_existing_images', source='daily_store')
    assert connector_platform_rule(task)['min_images'] >= 1
    with daily_content_delivery({'content_delivery': POLICY['content_delivery']}):
        assert connector_platform_rule(task)['min_images'] >= 1


def test_parallel_request_has_no_policy_leak():
    async def daily():
        with daily_content_delivery(POLICY):
            await asyncio.sleep(0)
            return connector_platform_rule(TaskInput(platform='moments'))['min_images']
    async def ordinary():
        await asyncio.sleep(0)
        return connector_platform_rule(TaskInput(platform='moments'))['min_images']
    async def both():
        return await asyncio.gather(daily(), ordinary())
    own, other = asyncio.run(both())
    assert own == 0 and other >= 1
