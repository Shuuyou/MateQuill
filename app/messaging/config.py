import importlib.util
import os
from dataclasses import dataclass
from urllib.parse import urlparse


@dataclass(frozen=True)
class ChannelConfig:
    channel: str
    label: str
    app_id: str
    secret: str
    sdk: str
    bot_id: str = ''
    encrypt_key: str = ''
    verification_token: str = ''

    @property
    def configured(self):
        return bool(self.app_id and self.secret)

    @property
    def connection_id(self):
        import hashlib
        return self.channel+':'+hashlib.sha256(self.app_id.encode()).hexdigest()[:24]


def channel_configs():
    def env(name): return os.getenv('MATEQUILL_'+name, '').strip()
    return {
        'wecom': ChannelConfig('wecom', '企业微信', env('WECOM_BOT_ID'), env('WECOM_BOT_SECRET'), 'aibot'),
        'feishu': ChannelConfig('feishu', '飞书', env('FEISHU_APP_ID'), env('FEISHU_APP_SECRET'), 'lark_oapi',
            env('FEISHU_BOT_OPEN_ID'), env('FEISHU_ENCRYPT_KEY'), env('FEISHU_VERIFICATION_TOKEN')),
        'dingtalk': ChannelConfig('dingtalk', '钉钉', env('DINGTALK_CLIENT_ID'), env('DINGTALK_CLIENT_SECRET'), 'dingtalk_stream'),
        'qq': ChannelConfig('qq', 'QQ', env('QQ_APP_ID'), env('QQ_APP_SECRET'), 'botpy'),
    }


def web_origin():
    value = os.getenv('MATEQUILL_PUBLIC_URL', '') or os.getenv('MATEQUILL_MESSAGING_WEB_ORIGIN', '')
    value = value.rstrip('/')
    parsed = urlparse(value)
    local = parsed.hostname in {'localhost', '127.0.0.1'} and parsed.scheme == 'http'
    if value and (parsed.scheme == 'https' or local) and parsed.hostname and not (
        parsed.username or parsed.password or parsed.query or parsed.fragment or parsed.path):
        return value
    return ''


def sdk_available(config):
    return importlib.util.find_spec(config.sdk) is not None
