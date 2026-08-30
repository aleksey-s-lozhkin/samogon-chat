from django.conf import settings


def turnstile(request):
    """Передаёт в шаблоны только публичный ключ виджета."""
    return {"turnstile_site_key": settings.TURNSTILE_SITE_KEY}
