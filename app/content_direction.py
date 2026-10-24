"""Reuse an authorized current opportunity as editorial input, never evidence.

The binding is execution-local and cannot be populated by a public TaskInput or
by parsing user text. Fresh product facts are checked after retrieval.
"""
from contextlib import contextmanager
from contextvars import ContextVar
from copy import deepcopy


_direction = ContextVar('matequill_prepared_content_direction', default=None)


def _matches(plan, task):
    return all(isinstance(plan.get(key), str) and plan[key] == getattr(task, field, None)
        for key, field in (('product', 'product'), ('audience_hypothesis', 'audience'),
                           ('customer_task', 'scene')))


@contextmanager
def prepared_content_direction(opportunity_id, *, conversation_id, task, audience='private'):
    """Re-authorize the current plan; unrelated tasks use normal exploration."""
    from .gtm_agent.marketing_opportunity import get_opportunity
    from .gtm_agent.repository import conversation_row

    conversation = conversation_row(conversation_id)['body']
    plan = get_opportunity(opportunity_id, audience=audience)
    current = conversation.get('current_opportunity') or {}
    eligible = (conversation.get('audience') == audience
        and plan.get('conversation_id') == conversation_id
        and current.get('id') == opportunity_id
        and plan.get('status') in {'prepared', 'content_ready'}
        and _matches(plan, task))
    projection = None
    if eligible:
        projection = {key: deepcopy(plan[key]) for key in (
            'id', 'product', 'audience_hypothesis', 'customer_task', 'content_angle',
            'visual_direction', 'fact_ids') if key in plan}
    token = _direction.set(projection)
    try:
        yield
    finally:
        _direction.reset(token)


def resolve_prepared_direction(task, bundle):
    """Bind only exact current task and fresh known fact IDs; no claims approval."""
    plan = _direction.get()
    if not isinstance(plan, dict) or not _matches(plan, task) or bundle.product != task.product:
        return None
    ids = plan.get('fact_ids')
    if (not isinstance(ids, list) or not ids or len(ids) > 4
            or any(not isinstance(identifier, str) for identifier in ids)
            or len(set(ids)) != len(ids) or set(ids) - {fact.id for fact in bundle.facts}):
        return None
    if any(not isinstance(plan.get(key), str) or not plan[key].strip()
           for key in ('id', 'content_angle', 'visual_direction')):
        return None
    # This is a prepared direction, not a fabricated 4-candidate exploration or
    # a claim that an already drafted headline passed independent review.
    return {'status': 'prepared_direction_reused', 'schema_version': 1,
        'selected_concept': {'angle': plan['content_angle'], 'payoff': plan['customer_task'],
            'customer_task': plan['customer_task'], 'visual_idea': plan['visual_direction'],
            'fact_ids': list(ids), 'asset_ids': []},
        'candidates': [], 'selection_reason': '', 'reason_code': '',
        'prepared_opportunity_id': plan['id'],
        'basis': 'authorized_current_opportunity_not_product_evidence'}
