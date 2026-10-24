"""Atomically link generated artifacts to a fenced Agent tool invocation."""
from contextlib import contextmanager
from contextvars import ContextVar
import time

from fastapi import HTTPException
from sqlalchemy import select

from ..tenancy import authorize_resource, service_update_body, set_resource_visibility, transaction_context
from .repository import TURN, artifact_reference

_effect = ContextVar('matequill_agent_effect', default=None)
_claim = ContextVar('matequill_agent_claim', default=None)


@contextmanager
def bind_effect(turn_id, key, audience, *, intermediate=False):
    token = _effect.set((turn_id, key, audience, intermediate))
    try:
        yield
    finally:
        _effect.reset(token)


def assert_agent_lease():
    claim = _claim.get()
    if not claim:
        return
    from .jobs import dispatch
    from ..tenancy.database import db_connection
    with db_connection() as connection:
        row = connection.execute(select(dispatch).where(dispatch.c.id == claim[0]).with_for_update()).mappings().first()
    if not row or row['claim_token'] != claim[1] or row['status'] != 'running' or row['lease_until'] <= time.time():
        raise HTTPException(409, '这次创作正在由另一工作进程继续')


def checkpoint_generated_artifact(kind, identifier, record):
    effect = _effect.get()
    if not effect:
        return
    # Blocked copy is never promoted into a completed deliverable.
    if kind == 'run' and record.get('status') != 'ready':
        return
    if kind == 'poster' and record.get('status') not in {'draft', 'ready', 'approved'}:
        return
    if kind == 'design' and record.get('status') != 'ready':
        return
    with transaction_context():
        assert_agent_lease()
        row = authorize_resource(TURN, effect[0])
        if effect[2] == 'group':
            set_resource_visibility(kind, identifier, 'store')
        reference = artifact_reference(kind, identifier, audience=effect[2])
        body = row['body']
        body = {**body, 'effects': {**body.get('effects', {}), effect[1]: reference}}
        if not effect[3]:
            body['artifact'] = reference
        service_update_body(TURN, effect[0], body, expected_version=row['version'])
