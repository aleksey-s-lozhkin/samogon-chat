import json

from django.conf import settings
from django.http import JsonResponse
from django.urls import reverse
from django.views.decorators.csrf import ensure_csrf_cookie
from django.views.decorators.http import require_GET, require_POST

from config.rate_limit import is_allowed
from users.models import PushSubscription
from users.services.push import (
    device_id_for_subscription,
    send_push_self_test,
)


def api_auth_error(request):
    if not request.user.is_authenticated:
        return JsonResponse({"error": "authentication_required"}, status=401)
    if request.user.is_banned:
        return JsonResponse({"error": "account_unavailable"}, status=403)
    return None


@require_GET
def api_status(request):
    """Минимальный health/status без секретов и персональных данных."""
    return JsonResponse(
        {
            "status": "ok",
            "api_version": "v1",
            "authenticated": request.user.is_authenticated,
            "web_push": {"configured": settings.WEB_PUSH_ENABLED},
        }
    )


@ensure_csrf_cookie
@require_GET
def api_push_subscriptions(request):
    """Возвращает безопасное состояние устройств текущего пользователя."""
    auth_error = api_auth_error(request)
    if auth_error:
        return auth_error

    session_endpoints = set(request.session.get("push_endpoints", []))
    subscriptions = PushSubscription.objects.filter(user=request.user)
    return JsonResponse(
        {
            "api_version": "v1",
            "web_push": {
                "configured": settings.WEB_PUSH_ENABLED,
                "subscriptions": [
                    {
                        "device_id": device_id_for_subscription(subscription),
                        "enabled": subscription.enabled,
                        "direct_messages_enabled": (
                            subscription.direct_messages_enabled
                        ),
                        "current_session": subscription.endpoint in session_endpoints,
                        "created_at": subscription.created_at.isoformat(),
                        "updated_at": subscription.updated_at.isoformat(),
                    }
                    for subscription in subscriptions
                ],
            },
        }
    )


@require_POST
def api_push_self_test(request):
    """Отправляет нейтральный тест на одно устройство текущего пользователя."""
    auth_error = api_auth_error(request)
    if auth_error:
        return auth_error
    if not settings.WEB_PUSH_ENABLED:
        return JsonResponse({"error": "web_push_unavailable"}, status=503)
    if not is_allowed(
        identifier=f"user:{request.user.id}",
        bucket="push-self-test",
        limit=settings.PUSH_SELF_TEST_RATE_LIMIT,
        window_seconds=settings.RATE_LIMIT_WINDOW_SECONDS,
    ):
        return JsonResponse({"error": "rate_limited"}, status=429)

    try:
        device_id = json.loads(request.body)["device_id"]
    except (KeyError, TypeError, ValueError, json.JSONDecodeError):
        return JsonResponse({"error": "invalid_request"}, status=400)
    if not isinstance(device_id, str) or not device_id:
        return JsonResponse({"error": "invalid_request"}, status=400)

    subscription = next(
        (
            item
            for item in PushSubscription.objects.filter(
                user=request.user,
                enabled=True,
            )
            if device_id_for_subscription(item) == device_id
        ),
        None,
    )
    if subscription is None:
        return JsonResponse({"error": "device_not_found"}, status=404)

    result = send_push_self_test(
        subscriptions=PushSubscription.objects.filter(pk=subscription.pk),
        url=reverse("profile"),
    )
    return JsonResponse(
        {
            "status": "accepted" if result.delivered else "failed",
            "accepted": result.delivered,
            "failed": result.failed,
            "removed": result.removed,
        }
    )
