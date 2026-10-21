"""Authorized creation history and deliberate store sharing."""
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from typing import Literal
from .tenancy import list_resources, audit
from .tenancy.resources import set_resource_visibility

router = APIRouter(prefix='/api/creations', tags=['creation-library'])


@router.get('')
def creations():
    items = []
    for kind in ('run', 'poster'):
        for row in list_resources(kind):
            body = row.get('body') or {}
            items.append({key: row.get(key) for key in (
                'id', 'kind', 'created_at', 'store_id', 'owner_id', 'visibility', 'version')}
            | {'title': body.get('title') or body.get('brief', {}).get('direction') or '门店创作',
               'status': body.get('status', 'draft')})
    return {'items': sorted(items, key=lambda row: row['created_at'], reverse=True)}


class SharingInput(BaseModel):
    visibility: Literal['private', 'store', 'tenant']


@router.patch('/{kind}/{identifier}/sharing')
def sharing(kind: str, identifier: str, payload: SharingInput):
    if kind not in ('run', 'poster', 'media'):
        raise HTTPException(404, '没有找到这份内容')
    row = set_resource_visibility(kind, identifier, payload.visibility)
    audit('resource.shared', kind, identifier, {'visibility': payload.visibility})
    return {key: row[key] for key in ('id', 'kind', 'visibility', 'version')}
