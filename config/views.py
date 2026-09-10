from django.http import HttpResponse, JsonResponse
from django.shortcuts import render
from django.templatetags.static import static
from drf_spectacular.utils import OpenApiResponse, extend_schema, inline_serializer
from rest_framework import serializers
from rest_framework.decorators import (
    api_view,
    authentication_classes,
    permission_classes,
)

from config.health import readiness_status


def health_response(payload, *, status=200):
    response = JsonResponse(payload, status=status)
    response["Cache-Control"] = "no-store"
    return response


@extend_schema(
    tags=("system",),
    responses={
        200: inline_serializer(
            name="LivenessResponse",
            fields={"status": serializers.CharField()},
        ),
    },
    auth=[],
)
@api_view(("GET",))
@authentication_classes(())
@permission_classes(())
def health_live(request):
    """Report that the ASGI process can accept an HTTP request."""
    return health_response({"status": "ok"})


readiness_serializer = inline_serializer(
    name="ReadinessResponse",
    fields={
        "status": serializers.CharField(),
        "components": serializers.DictField(child=serializers.CharField()),
    },
)


@extend_schema(
    tags=("system",),
    responses={
        200: readiness_serializer,
        503: OpenApiResponse(
            response=readiness_serializer,
            description="A required dependency is unavailable.",
        ),
    },
    auth=[],
)
@api_view(("GET",))
@authentication_classes(())
@permission_classes(())
def health_ready(request):
    """Report availability of the dependencies required by Web and WebSocket."""
    ready, components = readiness_status()
    return health_response(
        {
            "status": "ok" if ready else "unavailable",
            "components": components,
        },
        status=200 if ready else 503,
    )


def home(request):
    """Стартовая страница."""

    return render(
        request,
        "home.html",
    )


def service_rules(request):
    """Показывает публичные и понятные правила общения в сервисе."""
    return render(request, "service-rules.html")


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
