"""Небольшие общие лимиты для HTTP и WebSocket без отдельного сервиса."""

from hashlib import sha256

from django.conf import settings
from django.core.cache import cache


def client_ip(request) -> str:
    """Берёт адрес, установленный Nginx, а локально использует REMOTE_ADDR."""
    return request.META.get("HTTP_X_REAL_IP") or request.META.get(
        "REMOTE_ADDR", "unknown"
    )


def request_is_allowed(request, *, bucket: str, limit: int) -> bool:
    """Ограничивает запросы одного адреса в общем окне времени."""
    return is_allowed(
        identifier=f"ip:{client_ip(request)}",
        bucket=bucket,
        limit=limit,
        window_seconds=settings.RATE_LIMIT_WINDOW_SECONDS,
    )


def is_allowed(
    *, identifier: str, bucket: str, limit: int, window_seconds: int
) -> bool:
    """Возвращает, остался ли у пользователя запрос в текущем окне."""
    digest = sha256(f"{bucket}:{identifier}".encode()).hexdigest()
    cache_key = f"samogon:rate:{bucket}:{digest}"

    try:
        if cache.add(cache_key, 1, timeout=window_seconds):
            return True

        return cache.incr(cache_key) <= limit
    except Exception:
        # Проблема с cache не должна останавливать весь чат.
        return True
