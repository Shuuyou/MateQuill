from fastapi import APIRouter
from .models import ConversationInput, TurnInput, ArtifactLink
from .repository import create_conversation, conversations, get_conversation, get_turn, link_artifact
from .jobs import enqueue_turn

router = APIRouter(prefix='/api/agent', tags=['gtm-agent'])


@router.post('/conversations',status_code=201)
def create(payload: ConversationInput):
    return create_conversation(payload.title)


@router.get('/conversations')
def listing():
    return conversations()


@router.get('/conversations/{identifier}')
def conversation(identifier: str):
    return get_conversation(identifier)


@router.post('/conversations/{identifier}/turns',status_code=202)
def turn(identifier: str, payload: TurnInput):
    return enqueue_turn(identifier,payload)


@router.get('/conversations/{conversation_id}/turns/{identifier}')
def status(conversation_id: str, identifier: str):
    return get_turn(conversation_id,identifier)


@router.patch('/conversations/{identifier}/artifact')
def attach(identifier: str, payload: ArtifactLink):
    return link_artifact(identifier,payload.kind,payload.id,payload.expected_version,
        artifact_version=payload.version)
