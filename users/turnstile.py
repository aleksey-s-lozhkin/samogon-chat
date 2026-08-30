"""Проверка Cloudflare Turnstile без дополнительной зависимости."""

import json
from urllib.error import URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from django.conf import settings


TURNSTILE_VERIFY_URL = "https://challenges.cloudflare.com/turnstile/v0/siteverify"


def verify_turnstile(token: str, remote_ip: str | None) -> bool:
    """Подтверждает одноразовый токен на сервере, а не доверяет браузеру."""
    if not settings.TURNSTILE_ENABLED:
        return True
    if not token:
        return False

    payload = {
        "secret": settings.TURNSTILE_SECRET_KEY,
        "response": token,
    }
    if remote_ip:
        payload["remoteip"] = remote_ip

    request = Request(
        TURNSTILE_VERIFY_URL,
        data=urlencode(payload).encode(),
        method="POST",
    )
    try:
        with urlopen(request, timeout=5) as response:
            result = json.loads(response.read())
    except (OSError, URLError, ValueError, json.JSONDecodeError):
        return False

    return result.get("success") is True
