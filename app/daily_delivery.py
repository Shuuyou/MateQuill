"""Server-scoped daily text delivery; never an externally supplied task flag."""
from contextlib import contextmanager
from contextvars import ContextVar

_daily_text = ContextVar('daily_store_text_delivery', default=False)


@contextmanager
def daily_content_delivery(policy):
    enabled = (isinstance(policy, dict) and policy.get('source') == 'daily_store'
               and policy.get('content_delivery') == 'text_with_optional_existing_images')
    token = _daily_text.set(enabled)
    try:
        yield
    finally:
        _daily_text.reset(token)


def daily_text_delivery(platform):
    return platform == 'moments' and _daily_text.get()


def daily_platform_rule(platform, rule):
    if daily_text_delivery(platform):
        return {**rule, 'min_images': 0}
    return rule


def daily_delivery_record(platform, image_count):
    if not daily_text_delivery(platform):
        return None
    return {'requested': 'text_with_optional_existing_images',
            'delivered': 'text_with_existing_images' if image_count else 'text_only',
            'image_count': image_count, 'new_images_generated': False,
            'capture_plan_is_finished_image': False}
