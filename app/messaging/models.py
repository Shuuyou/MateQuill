from typing import Literal
from pydantic import BaseModel, ConfigDict, Field

Channel = Literal['wecom', 'feishu', 'dingtalk', 'qq']


class Attachment(BaseModel):
    model_config = ConfigDict(extra='forbid')
    kind: Literal['photo', 'audio']
    reference: str = Field(max_length=4096)
    aes_key: str = Field(default='', max_length=128)
    message_id: str = Field(default='', max_length=256)


class InboundEvent(BaseModel):
    """Constructed only after the official transport authenticates the event."""
    model_config = ConfigDict(extra='forbid')
    channel: Channel
    connection_id: str = Field(min_length=1, max_length=128)
    event_id: str = Field(min_length=1, max_length=256)
    sender_id: str = Field(min_length=1, max_length=256)
    chat_id: str = Field(min_length=1, max_length=256)
    audience: Literal['private', 'group'] = 'private'
    addressed: bool = True
    text: str = Field(default='', max_length=6000)
    attachments: list[Attachment] = Field(default_factory=list, max_length=4)
    reply_to_message_id: str = Field(default='', max_length=256)
    thread_id: str = Field(default='', max_length=256)
    response_req_id: str = Field(default='', max_length=256)
    response_url: str = Field(default='',max_length=4096)
    response_expires_at: float = 0
