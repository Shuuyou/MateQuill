"""Rules and qualification approval are distinct from Agent quote preparation."""
from fastapi import APIRouter
from pydantic import Field
from . import quotation as service

router = APIRouter(prefix='/api/quotations', tags=['authorized-quotations'])


class ApprovalInput(service.Contract):
    expected_version: int = Field(strict=True, ge=1)
    note: str = Field(default='', max_length=1000)


class UpdateInput(ApprovalInput):
    rule: service.RuleInput


@router.get('/rules')
def rules(store_id: str = '', sku: str = ''):
    return {'rules': service.list_rules(store_id=store_id, sku=sku)}


@router.post('/rules')
def create(payload: service.RuleInput):
    return service.create_rule(payload)


@router.put('/rules/{identifier}')
def update(identifier: str, payload: UpdateInput):
    return service.update_rule(identifier, payload.rule, expected_version=payload.expected_version)


@router.post('/rules/{identifier}/approve')
def approve(identifier: str, payload: ApprovalInput):
    return service.approve_rule(identifier, **payload.model_dump())


@router.post('/rules/{identifier}/revoke')
def revoke(identifier: str, payload: ApprovalInput):
    return service.revoke_rule(identifier, **payload.model_dump())


@router.post('/qualifications')
def qualification(payload: service.QualificationInput):
    return service.create_qualification(payload)


@router.post('/qualifications/{identifier}/approve')
def approve_qualification(identifier: str, payload: ApprovalInput):
    return service.approve_qualification(identifier, **payload.model_dump())


@router.post('/qualifications/{identifier}/revoke')
def revoke_qualification(identifier: str, payload: ApprovalInput):
    return service.revoke_qualification(identifier, **payload.model_dump())


@router.post('/prepare')
def prepare(payload: service.QuoteInput):
    return service.prepare_quote(**payload.model_dump())


@router.get('/{identifier}')
def read(identifier: str):
    return service.get_quote(identifier)
