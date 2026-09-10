import logging
import socket
from urllib.error import URLError
from urllib.request import urlopen

from django.core.cache import cache
from django.db import connection


logger = logging.getLogger(__name__)


def web_is_ready(timeout):
    """Check readiness through the same HTTP boundary used by Docker."""
    try:
        with urlopen(
            "http://127.0.0.1:8000/health/ready/",
            timeout=timeout,
        ) as response:
            return response.status == 200
    except (OSError, URLError):
        return False


def worker_is_ready(timeout):
    """Ping only the Celery worker running in the current container."""
    from config.celery import app

    worker_name = f"celery@{socket.gethostname()}"
    inspector = app.control.inspect(
        destination=[worker_name],
        timeout=timeout,
    )
    response = inspector.ping() or {}
    return response.get(worker_name, {}).get("ok") == "pong"


def check_database():
    """Verify that Django can execute a minimal query."""
    try:
        with connection.cursor() as cursor:
            cursor.execute("SELECT 1")
            cursor.fetchone()
    except Exception as error:  # pragma: no cover - the backend defines failures
        connection.close()
        logger.warning(
            "Readiness check failed for database (%s)",
            type(error).__name__,
        )
        return False
    return True


def check_cache():
    """Verify the configured cache transport without writing application data."""
    try:
        cache.get("samogon:health:ready")
    except Exception as error:  # pragma: no cover - the backend defines failures
        logger.warning(
            "Readiness check failed for cache (%s)",
            type(error).__name__,
        )
        return False
    return True


def readiness_status():
    components = {
        "database": "ok" if check_database() else "unavailable",
        "redis": "ok" if check_cache() else "unavailable",
    }
    ready = all(status == "ok" for status in components.values())
    return ready, components
