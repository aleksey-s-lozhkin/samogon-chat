from django.conf import settings


def turnstile(request):
    """Передаёт в шаблоны только публичный ключ виджета."""
    return {"turnstile_site_key": settings.TURNSTILE_SITE_KEY}


def oauth_providers(request):
    """Показывает OAuth-кнопки только при полной настройке провайдера."""
    return {
        "github_oauth_enabled": bool(
            settings.GITHUB_OAUTH_CLIENT_ID
            and settings.GITHUB_OAUTH_CLIENT_SECRET
        ),
        "google_oauth_enabled": bool(
            settings.GOOGLE_OAUTH_CLIENT_ID
            and settings.GOOGLE_OAUTH_CLIENT_SECRET
        ),
    }
