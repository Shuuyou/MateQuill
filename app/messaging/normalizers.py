"""Normalize authenticated official SDK callbacks. Never consume ambient group chat."""
import json
from .models import Attachment, InboundEvent


def wecom(frame, config):
    body = frame.get('body', {})
    if frame.get('cmd') != 'aibot_msg_callback' or body.get('aibotid') != config.app_id:
        return None
    kind = body.get('msgtype')
    items = body.get('mixed', {}).get('msg_item', []) if kind == 'mixed' else [body]
    text, attachments = [], []
    for item in items:
        item_kind = item.get('msgtype', kind)
        if item_kind == 'text':
            text.append(item.get('text', {}).get('content', ''))
        elif item_kind == 'voice':
            text.append(item.get('voice', {}).get('content', ''))
        elif item_kind == 'image':
            image = item.get('image', {})
            attachments.append(Attachment(kind='photo', reference=image['url'], aes_key=image['aeskey']))
    sender = body.get('from', {}).get('userid')
    if not sender or not body.get('msgid') or (not text and not attachments):
        return None
    audience = 'group' if body.get('chattype') == 'group' else 'private'
    return InboundEvent(channel='wecom', connection_id=config.connection_id,
        event_id=body['msgid'], sender_id=sender, chat_id=body.get('chatid') or sender,
        audience=audience, text='\n'.join(text), attachments=attachments[:4],
        response_req_id=frame.get('headers', {}).get('req_id', ''))


def feishu(payload, config):
    event = payload.get('event', {})
    message = event.get('message', {})
    sender = event.get('sender', {}).get('sender_id', {}).get('open_id')
    if not sender or not message.get('message_id'):
        return None
    group = message.get('chat_type') != 'p2p'
    if group and not (config.bot_id and any(m.get('id', {}).get('open_id') == config.bot_id for m in message.get('mentions', []))):
        return None
    content = json.loads(message.get('content', '{}'))
    kind, attachments = message.get('message_type'), []
    if kind in {'image', 'audio'}:
        key = content.get('image_key' if kind == 'image' else 'file_key')
        if key:
            attachments.append(Attachment(kind='photo' if kind == 'image' else 'audio', reference=key, message_id=message['message_id']))
    return InboundEvent(channel='feishu', connection_id=config.connection_id,
        event_id=message['message_id'],sender_id=sender,chat_id=message['chat_id'],
        audience='group' if group else 'private',text=content.get('text',''),attachments=attachments)


def dingtalk(payload, config):
    group = payload.get('conversationType') == '2'
    if group and not payload.get('isInAtList'):
        return None
    sender = payload.get('senderStaffId')
    if not sender or not payload.get('msgId'):
        return None
    content = payload.get('content', {})
    kind = payload.get('msgtype')
    text = payload.get('text', {}).get('content', '') if kind == 'text' else content.get('recognition', '')
    attachments = []
    if kind == 'picture' and content.get('downloadCode'):
        attachments.append(Attachment(kind='photo',reference=content['downloadCode']))
    return InboundEvent(channel='dingtalk',connection_id=config.connection_id,
        event_id=payload['msgId'],sender_id=sender,chat_id=payload.get('conversationId') or sender,
        audience='group' if group else 'private',text=text,attachments=attachments,
        response_url=payload.get('sessionWebhook',''),response_expires_at=float(payload.get('sessionWebhookExpiredTime',0))/1000)
