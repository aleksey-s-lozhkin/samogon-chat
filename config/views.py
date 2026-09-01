from django.http import HttpResponse, JsonResponse
from django.shortcuts import render
from django.templatetags.static import static


def home(request):
    """Стартовая страница."""

    return render(
        request,
        "home.html",
    )


def robots(request):
    """Не индексирует закрытую бета-версию и убирает лишнее предупреждение."""
    return HttpResponse(
        "User-agent: *\nDisallow: /\n",
        content_type="text/plain",
    )


def pwa_manifest(request):
    """Возвращает манифест устанавливаемого приложения."""
    response = JsonResponse(
        {
            "name": "Самогон — барный чат",
            "short_name": "Самогон",
            "description": "Барный чат для разработчиков.",
            "lang": "ru",
            "start_url": "/",
            "scope": "/",
            "display": "standalone",
            "background_color": "#1a1a1a",
            "theme_color": "#1a1a1a",
            "icons": [
                {
                    "src": static("pwa/icon-192.png"),
                    "sizes": "192x192",
                    "type": "image/png",
                    "purpose": "any maskable",
                },
                {
                    "src": static("pwa/icon-512.png"),
                    "sizes": "512x512",
                    "type": "image/png",
                    "purpose": "any maskable",
                },
            ],
        },
        content_type="application/manifest+json",
    )
    response["Cache-Control"] = "no-cache"
    return response


def service_worker(request):
    """Отдаёт service worker из корня, чтобы он видел весь сайт."""
    response = render(
        request,
        "pwa/service-worker.js",
        content_type="application/javascript",
    )
    response["Cache-Control"] = "no-cache"
    response["Service-Worker-Allowed"] = "/"
    return response


def offline(request):
    """Показывает спокойную заглушку при отсутствии сети."""
    return render(request, "pwa/offline.html")
