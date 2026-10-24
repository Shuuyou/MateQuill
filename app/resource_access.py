"""Shared enforcement for the existing creation, media and export workflows."""
from .tenancy import get_context, authorize_resource


def authorize_if_scoped(kind, identifier, action='read'):
    context = get_context()
    if context and not context.is_legacy:
        return authorize_resource(kind, identifier, action=action)


def ensure_live_context():
    from fastapi import HTTPException
    from .tenancy import refresh_context, set_context
    context = get_context()
    if context and not context.is_legacy:
        fresh = refresh_context(context)
        if fresh is None:
            raise HTTPException(403, '工作空间权限已更新，请重新登录')
        set_context(fresh)
        return fresh
    return context


def validate_task_resources(task):
    """Authorize every upload before it can enter retrieval or a model prompt."""
    from fastapi import HTTPException
    from .generation_jobs import assert_job_lease
    assert_job_lease()
    from .gtm_agent.effects import assert_agent_lease
    assert_agent_lease()
    context = ensure_live_context()
    if context and not context.is_legacy and task.store_id and task.store_id != context.store_id:
        raise HTTPException(409, '请先切换到这家门店，再开始创作')
    if context and not context.is_legacy and task.assignment_id:
        from .channel_workflows import validate_generation_task
        validate_generation_task(task)
    for identifier in task.media_ids:
        meta = authorize_if_scoped('media', identifier)
        if context and not context.is_legacy and meta and meta.get('visibility') != 'tenant' and context.store_id and meta.get('store_id') not in (None, context.store_id):
            raise HTTPException(422, '请选择当前门店的素材，或先切换到素材所属门店')


def request_budget_key(request):
    context = get_context()
    if context and not context.is_legacy:
        return context.tenant_id + ':' + context.user_id
    return request.client.host if request.client else 'unknown'
