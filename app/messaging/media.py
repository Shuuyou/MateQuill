"""Platform-owned download URLs only, with no redirects and a streaming byte cap."""
import base64
from urllib.parse import urlparse
import httpx

MAX_BYTES = 12 * 1024 * 1024


def allowed_url(url, domains):
    parsed = urlparse(url)
    host = (parsed.hostname or '').lower()
    return parsed.scheme == 'https' and not parsed.username and not parsed.password and not parsed.fragment and parsed.port in (None,443) and any(host == d or host.endswith('.'+d) for d in domains)


async def download(url, domains, *, headers=None):
    if not allowed_url(url, domains):
        raise ValueError('unsupported_media_host')
    async with httpx.AsyncClient(timeout=25, follow_redirects=False) as client:
        async with client.stream('GET', url, headers=headers) as response:
            response.raise_for_status()
            if response.status_code != 200 or int(response.headers.get('content-length', '0')) > MAX_BYTES:
                raise ValueError('invalid_media_size')
            result = bytearray()
            async for chunk in response.aiter_bytes():
                result.extend(chunk)
                if len(result) > MAX_BYTES:
                    raise ValueError('invalid_media_size')
    return bytes(result)


def decrypt_wecom(raw, aes_key):
    from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
    key = base64.b64decode(aes_key + '=' * (-len(aes_key) % 4), validate=True)
    if len(key) != 32 or not raw or len(raw) % 16:
        raise ValueError('invalid_media_encryption')
    decryptor = Cipher(algorithms.AES(key), modes.CBC(key[:16])).decryptor()
    plain = decryptor.update(raw) + decryptor.finalize()
    padding = plain[-1]
    if not 1 <= padding <= 32 or plain[-padding:] != bytes([padding])*padding:
        raise ValueError('invalid_media_padding')
    return plain[:-padding]
