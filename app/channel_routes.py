"""Channel collaboration routes. Identity and scope come from tenant middleware."""
from fastapi import APIRouter
from fastapi.responses import Response
from . import channel_workflows as service

router = APIRouter(prefix='/api/channel', tags=['channel-collaboration'])


@router.get('/campaigns')
def campaigns():
    return {'campaigns': service.list_campaigns(), 'progress': service.campaign_progress()}


@router.post('/campaigns', status_code=201)
def create_campaign(payload: service.CampaignPackageInput):
    return service.create_campaign(payload)


@router.put('/campaigns/{identifier}')
def update_campaign(identifier: str, payload: service.CampaignPackageInput):
    return service.update_campaign(identifier, payload)


@router.post('/campaigns/{identifier}/publish')
def publish_campaign(identifier: str):
    return service.publish_campaign(identifier)


@router.post('/campaigns/{identifier}/withdraw')
def withdraw_campaign(identifier: str):
    return service.withdraw_campaign(identifier)


@router.get('/assignments')
def assignments():
    return {'assignments': service.list_assignments()}


@router.post('/assignments/{identifier}/accept')
def accept_assignment(identifier: str):
    return service.accept_assignment(identifier)


@router.get('/assignments/{identifier}/task')
def task(identifier: str):
    return service.assignment_task(identifier)


@router.get('/submissions')
def submissions():
    return {'submissions': service.list_submissions()}


@router.post('/submissions', status_code=201)
def submit_content(payload: service.SubmissionInput):
    return service.submit_content(payload)


@router.post('/submissions/{identifier}/review')
def review(identifier: str, payload: service.ReviewInput):
    return service.review_submission(identifier, payload)


@router.post('/submissions/{identifier}/publish')
def publication(identifier: str, payload: service.PublicationInput):
    return service.publish_submission(identifier, payload)


@router.get('/grants')
def grants():
    return {'grants': service.list_grants()}


@router.post('/campaigns/{identifier}/grants', status_code=201)
def share(identifier: str, payload: service.GrantInput):
    return service.create_grant(identifier, payload)


@router.post('/grants/accept')
def accept_grant(payload: service.AcceptGrantInput):
    return service.accept_grant(payload)


@router.post('/grants/{identifier}/revoke')
def revoke_grant(identifier: str):
    return service.revoke_grant(identifier)


@router.get('/submissions/{identifier}/assets/{asset_id}')
async def submission_asset(identifier: str, asset_id: str):
    image = service.submission_asset(identifier, asset_id)
    if image is not None:
        return Response(image[0], media_type=image[1], headers={
            'Cache-Control': 'private, no-store', 'X-Content-Type-Options': 'nosniff'})
    from .api import asset
    response = await asset(asset_id)
    response.headers['Cache-Control'] = 'private, no-store'
    return response
