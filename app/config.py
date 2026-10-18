"""Process-local settings. Credentials are never serialized by public endpoints."""
import os
from dataclasses import dataclass, field
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

@dataclass
class Settings:
    ima_client_id: str = field(default_factory=lambda: os.getenv('IMA_OPENAPI_CLIENTID', ''))
    ima_api_key: str = field(default_factory=lambda: os.getenv('IMA_OPENAPI_APIKEY', ''))
    ima_kb_name: str = field(default_factory=lambda: os.getenv('IMA_KB_NAME', '任德耀新建华为PC知识库'))
    ima_kb_id: str = field(default_factory=lambda: os.getenv('IMA_KB_ID', ''))
    llm_base_url: str = field(default_factory=lambda: os.getenv('LLM_BASE_URL', 'https://api.deepseek.com'))
    llm_api_key: str = field(default_factory=lambda: os.getenv('LLM_API_KEY', ''))
    llm_model: str = field(default_factory=lambda: os.getenv('LLM_MODEL', 'deepseek-flash'))
    image_base_url: str = field(default_factory=lambda: os.getenv('IMAGE_BASE_URL', 'https://api.openai-next.com/v1'))
    image_api_key: str = field(default_factory=lambda: os.getenv('IMAGE_API_KEY', ''))
    image_model: str = field(default_factory=lambda: os.getenv('IMAGE_MODEL', 'gpt-image-2.5'))
    image_review_model: str = field(default_factory=lambda: os.getenv('IMAGE_REVIEW_MODEL', ''))
    allow_local_settings: bool = field(default_factory=lambda: os.getenv('ALLOW_LOCAL_SETTINGS', '1') == '1')
    max_media_bytes: int = 12 * 1024 * 1024
    max_directory_items: int = 1800

    @property
    def ima_ready(self):
        return bool(self.ima_client_id and self.ima_api_key)

    def public(self):
        return {'ima_configured': self.ima_ready, 'llm_configured': bool(self.llm_api_key),
                'image_configured': bool(self.image_api_key), 'image_model': self.image_model,
                'image_review_model': self.image_review_model,
                'knowledge_base': self.ima_kb_name, 'llm_model': self.llm_model,
                'local_settings_enabled': self.allow_local_settings}

settings = Settings()
