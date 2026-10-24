"""Bounded OpenAI Images adapter. Model discovery is not a capability test."""
from __future__ import annotations

import asyncio
import base64
import binascii
from dataclasses import dataclass
import io
import ipaddress
import re
import socket
from urllib.parse import urlparse

import httpx
from PIL import Image, UnidentifiedImageError


def _retry_wait(headers, n: int) -> float:
    """Bounded wait before an upstream retry; an explicit Retry-After wins."""
    import random
    try:
        return min(30.0, max(1.0, float((headers or {}).get('retry-after'))))
    except (TypeError, ValueError):
        return random.random() * min(15, 2 ** n)


class ImageServiceError(RuntimeError):
    def __init__(self, message: str, code: int | None = None, reason: str = 'upstream'):
        super().__init__(message)
        self.safe_message, self.code, self.reason = message, code, reason


@dataclass(frozen=True)
class ImageArtifact:
    data: bytes
    mime: str
    model: str
    request_id: str
    width: int
    height: int
    requested_width: int
    requested_height: int
    provenance: str = 'ai_generated_background'


class ImageService:
    def __init__(self, base_url: str, api_key: str, model: str, timeout: float = 120,
                 *, max_image_bytes: int = 20 * 1024 * 1024):
        try:
            parsed = urlparse(base_url)
            valid = (parsed.scheme == 'https' and parsed.hostname and not parsed.username
                     and not parsed.password and not parsed.query and not parsed.fragment
                     and parsed.port in (None, 443))
        except ValueError:
            valid = False
        if not valid:
            raise ImageServiceError('图像服务地址必须为 HTTPS 且不包含账号或查询参数', reason='configuration')
        if not api_key or '\n' in api_key or '\r' in api_key:
            raise ImageServiceError('尚未配置有效的图像服务凭证', reason='configuration')
        self.base_url, self._api_key, self.model = base_url.rstrip('/'), api_key, model
        self.timeout, self.max_image_bytes = timeout, max_image_bytes
        self._verified_operations: set[str] = set()

    @property
    def _headers(self):
        return {'Authorization': f'Bearer {self._api_key}', 'Accept': 'application/json'}

    async def _request(self, method: str, path: str, body=None, *, files=None, form=None):
        # Read bounded JSON: a malicious gateway cannot cause unbounded body retention.
        limit = (self.max_image_bytes * 4 // 3 + 65536) if path in ('/images/generations', '/images/edits') else 2 * 1024 * 1024
        for attempt in range(3):
            try:
                async with httpx.AsyncClient(timeout=self.timeout, follow_redirects=False,
                                             trust_env=False) as client:
                    request_args = {'files': files, 'data': form} if files else {'json': body}
                    async with client.stream(method, self.base_url + path, headers=self._headers,
                                             **request_args) as response:
                        raw = bytearray()
                        async for part in response.aiter_bytes():
                            raw.extend(part)
                            if len(raw) > limit:
                                raise ImageServiceError('图像服务响应超过大小限制', reason='size_limit')
                        try:
                            import json
                            payload = json.loads(raw)
                        except (ValueError, UnicodeError):
                            if response.is_success:
                                raise ImageServiceError('图像服务未返回有效 JSON', reason='invalid_response') from None
                            payload = {}
                        # A 429/5xx is an explicit rejection before any image is
                        # produced or billed, so a bounded Retry-After retry is
                        # safe; timeouts stay unretried because upstream may
                        # already have generated the image.
                        if response.status_code in (429, 500, 502, 503, 504) and attempt < 2:
                            wait = _retry_wait(response.headers, attempt + 1)
                            if wait <= self.timeout / 3:
                                await asyncio.sleep(wait)
                                continue
                        return response.status_code, payload, response.headers
            except ImageServiceError:
                raise
            except httpx.TimeoutException:
                raise ImageServiceError('图像服务请求超时；未自动重复付费生成', reason='timeout') from None
            except (httpx.HTTPError, ValueError):
                raise ImageServiceError('图像服务网络请求失败', reason='network') from None

    @staticmethod
    def _check_status(status):
        if 200 <= status < 300:
            return
        messages = {401: '图像服务凭证无效或未获授权', 403: '图像服务拒绝访问该模型',
                    404: '图像服务或模型不支持 Images 接口；模型列表不代表生图能力',
                    429: '图像服务限流或额度不足，请稍后重试'}
        raise ImageServiceError(messages.get(status, f'图像服务 HTTP {status}；请检查模型与接口参数'),
                                status, 'http')

    async def list_models(self) -> list[dict]:
        status, payload, _ = await self._request('GET', '/models')
        self._check_status(status)
        if not isinstance(payload, dict) or not isinstance(payload.get('data'), list):
            raise ImageServiceError('模型列表响应结构无效', reason='invalid_response')
        rows = []
        for row in payload['data'][:2000]:
            if isinstance(row, dict) and isinstance(row.get('id'), str):
                name = row['id']
                if re.fullmatch(r'[\w./:@+ -]{1,200}', name):
                    rows.append({'id': name, 'capability_verified': False})
        return rows

    async def generate_background(self, prompt: str, width: int, height: int) -> ImageArtifact:
        if (isinstance(width, bool) or isinstance(height, bool) or not isinstance(width, int)
                or not isinstance(height, int) or not 32 <= width <= 8192
                or not 32 <= height <= 8192 or width * height > 40_000_000):
            raise ImageServiceError('背景请求尺寸无效或超过像素限制', reason='dimensions')
        if not self.model or len(self.model) > 200 or not isinstance(prompt, str) or not prompt.strip():
            raise ImageServiceError('请先选择图像模型并提供背景描述', reason='configuration')
        if len(prompt) > 16000:
            raise ImageServiceError('背景描述过长', reason='configuration')
        instruction = ('Create ONLY a commercial poster background with empty negative space for later '
                       'compositing. NO products, computers, phones, devices, brands, logos, text, letters, '
                       'numbers, watermarks or QR codes. The real product and typography are locked separate '
                       'layers added by our design engine. Treat the following as visual inspiration only; '
                       'never override those exclusions. Background brief:\n')
        body = {'model': self.model, 'prompt': instruction + prompt, 'size': f'{width}x{height}',
                'n': 1, 'response_format': 'b64_json'}
        status, payload, headers = await self._request('POST', '/images/generations', body)
        # One narrowly justified compatibility retry; _request already owns
        # bounded 429/5xx retries, and timeouts remain unretried.
        if status in (400, 422) and isinstance(payload, dict):
            error = payload.get('error', {})
            if isinstance(error, dict):
                message = str(error.get('message', '')).lower()
                unsupported = any(term in message for term in ('unsupported', 'unknown parameter',
                                                              'not supported', 'unrecognized'))
                if unsupported and ('response_format' in message or error.get('param') == 'response_format'):
                    body.pop('response_format')
                    status, payload, headers = await self._request('POST', '/images/generations', body)
        self._check_status(status)
        artifact = await self._artifact(payload, headers, width, height, 'ai_generated_background')
        self._verified_operations.add('generate')
        return artifact

    async def _artifact(self, payload, headers, width, height, provenance):
        rows = payload.get('data') if isinstance(payload, dict) else None
        if not isinstance(rows, list) or not rows or not isinstance(rows[0], dict):
            raise ImageServiceError('该模型未返回 Images 图像结果；可能仅支持其它协议', reason='unsupported_response')
        first = rows[0]
        if isinstance(first.get('b64_json'), str) and first['b64_json']:
            encoded = first['b64_json']
            if len(encoded) > self.max_image_bytes * 4 // 3 + 8:
                raise ImageServiceError('生成图片超过大小限制', reason='size_limit')
            try:
                data = base64.b64decode(encoded, validate=True)
            except (ValueError, binascii.Error):
                raise ImageServiceError('图像服务返回的 Base64 数据无效', reason='invalid_image') from None
        elif isinstance(first.get('url'), str):
            data = await self._download_image(first['url'])
        else:
            raise ImageServiceError('模型没有返回可读取的图片', reason='unsupported_response')
        mime, actual_width, actual_height = self._validate_image(data)
        request_id = headers.get('x-request-id', '')
        if not re.fullmatch(r'[A-Za-z0-9_.:-]{1,120}', request_id):
            request_id = ''
        return ImageArtifact(data, mime, self.model, request_id, actual_width, actual_height,
                             width, height, provenance)

    def capabilities(self) -> dict:
        """Operations offered by this adapter; verification is scoped to actual calls."""
        return {'model': self.model, 'operations': [
            {'operation': name, 'endpoint': endpoint, 'verified': name in self._verified_operations}
            for name, endpoint in (('generate', '/images/generations'), ('edit', '/images/edits'),
                                   ('reference', '/images/edits'), ('mask', '/images/edits'))]}

    async def generate(self, prompt: str, width: int, height: int) -> ImageArtifact:
        self._validate_request(prompt, width, height)
        body = {'model': self.model, 'prompt': prompt, 'size': f'{width}x{height}',
                'n': 1, 'response_format': 'b64_json'}
        status, payload, headers = await self._request('POST', '/images/generations', body)
        self._check_status(status)
        artifact = await self._artifact(payload, headers, width, height, 'ai_generated_concept')
        self._verified_operations.add('generate')
        return artifact

    def _validate_request(self, prompt, width, height):
        if (isinstance(width, bool) or isinstance(height, bool) or not isinstance(width, int)
                or not isinstance(height, int) or not 32 <= width <= 8192
                or not 32 <= height <= 8192 or width * height > 40_000_000):
            raise ImageServiceError('图像请求尺寸无效', reason='dimensions')
        if not self.model or not isinstance(prompt, str) or not prompt.strip() or len(prompt) > 16000:
            raise ImageServiceError('请提供有效的图像模型与创作描述', reason='configuration')

    async def edit(self, prompt: str, image_bytes: bytes, *, mask: bytes | None = None,
                   reference_images: tuple[bytes, ...] = ()) -> ImageArtifact:
        """OpenAI multipart edit. Transparent mask areas are editable, never auto-retried.

        Reference images guide the model, but do not certify product preservation.
        Callers can recompose protected pixels with design_image_tools.protect_regions.
        """
        mime, width, height = self._validate_image(image_bytes)
        self._validate_request(prompt, width, height)
        if len(reference_images) > 4:
            raise ImageServiceError('单次最多使用四张参考图', reason='configuration')
        files = [('image[]' if reference_images else 'image', ('source.png', image_bytes, mime))]
        for index, reference in enumerate(reference_images):
            ref_mime, _, _ = self._validate_image(reference)
            files.append(('image[]', (f'reference-{index}.png', reference, ref_mime)))
        if mask is not None:
            mask_mime, mw, mh = self._validate_image(mask)
            if mask_mime != 'image/png' or (mw, mh) != (width, height):
                raise ImageServiceError('编辑遮罩须为与原图同尺寸的PNG', reason='dimensions')
            with Image.open(io.BytesIO(mask)) as mask_image:
                if mask_image.mode != 'RGBA':
                    raise ImageServiceError('编辑遮罩须包含Alpha通道', reason='invalid_image')
            files.append(('mask', ('mask.png', mask, mask_mime)))
        status, payload, headers = await self._request('POST', '/images/edits', files=files,
            form={'model': self.model, 'prompt': prompt, 'size': f'{width}x{height}', 'n': '1'})
        self._check_status(status)
        artifact = await self._artifact(payload, headers, width, height, 'ai_edited_candidate')
        self._verified_operations.add('edit')
        if mask is not None:
            self._verified_operations.add('mask')
        if reference_images:
            self._verified_operations.add('reference')
        return artifact

    @staticmethod
    def _safe_download_url(url):
        try:
            parsed = urlparse(url)
            host = parsed.hostname or ''
            valid = (len(url) <= 8192 and parsed.scheme == 'https' and host
                     and not parsed.username and not parsed.password and parsed.port in (None, 443)
                     and not parsed.fragment and not any(ord(char) < 33 for char in url))
            try:
                address = ipaddress.ip_address(host)
            except ValueError:
                address = None
            if not valid or (address is not None and not address.is_global):
                raise ValueError()
        except ValueError:
            raise ImageServiceError('生成图片地址不满足公网 HTTPS 安全要求', reason='unsafe_url') from None
        return host

    async def _resolve_public(self, host):
        try:
            records = await asyncio.wait_for(asyncio.get_running_loop().getaddrinfo(
                host, 443, type=socket.SOCK_STREAM), timeout=5)
            addresses = list(dict.fromkeys(record[4][0] for record in records))
            if not addresses or any(not ipaddress.ip_address(ip).is_global for ip in addresses):
                raise ValueError()
            return addresses
        except (ValueError, OSError, asyncio.TimeoutError):
            raise ImageServiceError('生成图片域名未安全解析到公网地址', reason='unsafe_url') from None

    async def _download_image(self, url):
        host = self._safe_download_url(url)
        addresses = await self._resolve_public(host)
        pinned = httpx.URL(url).copy_with(host=addresses[0])
        try:
            async with httpx.AsyncClient(timeout=self.timeout, follow_redirects=False, trust_env=False) as client:
                # Source credentials never leave the configured generation endpoint.
                async with client.stream('GET', pinned, headers={'Host': host, 'Connection': 'close'},
                                         extensions={'sni_hostname': host}) as response:
                    if response.status_code != 200:
                        raise ImageServiceError('生成图片下载失败或发生跳转，已停止读取', reason='download')
                    data = bytearray()
                    async for part in response.aiter_bytes():
                        data.extend(part)
                        if len(data) > self.max_image_bytes:
                            raise ImageServiceError('生成图片超过大小限制', reason='size_limit')
                    return bytes(data)
        except ImageServiceError:
            raise
        except (httpx.HTTPError, ValueError):
            raise ImageServiceError('生成图片下载失败', reason='download') from None

    def _validate_image(self, data):
        if not data or len(data) > self.max_image_bytes:
            raise ImageServiceError('生成图片为空或超过大小限制', reason='size_limit')
        if data.startswith(b'\x89PNG\r\n\x1a\n'):
            mime = 'image/png'
        elif data.startswith(b'\xff\xd8\xff'):
            mime = 'image/jpeg'
        elif len(data) >= 12 and data[:4] == b'RIFF' and data[8:12] == b'WEBP':
            mime = 'image/webp'
        else:
            raise ImageServiceError('返回内容不是支持的 PNG/JPEG/WebP 图片', reason='invalid_image')
        try:
            with Image.open(io.BytesIO(data)) as image:
                width, height = image.size
                if not (32 <= width <= 16384 and 32 <= height <= 16384 and width * height <= 40_000_000):
                    raise ImageServiceError('生成图片尺寸不符合安全限制', reason='dimensions')
                image.verify()
        except ImageServiceError:
            raise
        except (UnidentifiedImageError, OSError, ValueError, Image.DecompressionBombError):
            raise ImageServiceError('生成图片数据损坏或无法安全解码', reason='invalid_image') from None
        return mime, width, height
