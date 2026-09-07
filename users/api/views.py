from django.conf import settings
from django.http import JsonResponse
from django.urls import reverse
from django.views.decorators.csrf import ensure_csrf_cookie
from asgiref.sync import async_to_sync
from channels.layers import get_channel_layer
from drf_spectacular.utils import OpenApiResponse, extend_schema
from rest_framework.decorators import api_view

from config.rate_limit import is_allowed
from users.models import PushSubscription
from users.services.push import device_id_for_subscription, send_push_self_test
from chat.consumers import ChatConsumer, PRESENCE_GROUP_NAME
from chat.services.presence import online_users

from .serializers import (
    ErrorSerializer,
    PushSelfTestRequestSerializer,
    PushSelfTestResponseSerializer,
    PushSubscriptionsResponseSerializer,
    StatusSerializer,
    CurrentUserSerializer,
    PresenceStatusUpdateSerializer,
)


def api_auth_error(request):
    if not request.user.is_authenticated:
        return JsonResponse({"error": "authentication_required"}, status=401)
    if request.user.is_banned:
        return JsonResponse({"error": "account_unavailable"}, status=403)
    return None


@extend_schema(
    tags=("system",),
    responses={200: StatusSerializer},
    auth=[],
)
@api_view(("GET",))
def api_status(request):
    """Return safe application state without secrets or personal data."""
    return JsonResponse(
        {
            "status": "ok",
            "api_version": "v1",
            "authenticated": request.user.is_authenticated,
            "web_push": {"configured": settings.WEB_PUSH_ENABLED},
        }
    )


def current_user_data(user):
    return {
        "username": user.username,
        "avatar_url": user.avatar.url if user.avatar else None,
        "message_color": user.message_color,
        "presence_status": user.presence_status,
        "presence_status_label": user.get_presence_status_display(),
    }


@extend_schema(
    tags=("users",),
    auth=({"cookieAuth": []},),
    request=PresenceStatusUpdateSerializer,
    responses={200: CurrentUserSerializer, 400: ErrorSerializer, 401: ErrorSerializer, 403: ErrorSerializer},
)
@api_view(("GET", "PATCH"))
def api_current_user(request):
    if error := api_auth_error(request):
        return error
    if request.method == "PATCH":
        serializer = PresenceStatusUpdateSerializer(data=request.data)
        if not serializer.is_valid():
            return JsonResponse({"error": "invalid_request"}, status=400)
        request.user.presence_status = serializer.validated_data["presence_status"]
        request.user.save(update_fields=("presence_status",))
        async_to_sync(get_channel_layer().group_send)(
            PRESENCE_GROUP_NAME,
            {
                "type": "presence_update",
                "users": async_to_sync(ChatConsumer().get_all_users)(),
                "online": async_to_sync(online_users.get_all_users)(),
            },
        )
    return JsonResponse(current_user_data(request.user))


@extend_schema(
    tags=("web-push",),
    auth=({"cookieAuth": []},),
    responses={
        200: PushSubscriptionsResponseSerializer,
        401: ErrorSerializer,
        403: ErrorSerializer,
    },
)
@ensure_csrf_cookie
@api_view(("GET",))
def api_push_subscriptions(request):
    """Return opaque device state belonging to the current user."""
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
                        "direct_messages_enabled": subscription.direct_messages_enabled,
                        "current_session": subscription.endpoint in session_endpoints,
                        "created_at": subscription.created_at.isoformat(),
                        "updated_at": subscription.updated_at.isoformat(),
                    }
                    for subscription in subscriptions
                ],
            },
        }
    )


@extend_schema(
    tags=("web-push",),
    auth=({"cookieAuth": []},),
    request=PushSelfTestRequestSerializer,
    responses={
        200: PushSelfTestResponseSerializer,
        400: ErrorSerializer,
        401: ErrorSerializer,
        403: ErrorSerializer,
        404: ErrorSerializer,
        429: ErrorSerializer,
        503: ErrorSerializer,
    },
)
@api_view(("POST",))
def api_push_self_test(request):
    """Send a neutral test notification to one device of the current user."""
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

    serializer = PushSelfTestRequestSerializer(data=request.data)
    if not serializer.is_valid():
        return JsonResponse({"error": "invalid_request"}, status=400)
    device_id = serializer.validated_data["device_id"]

    subscription = next(
        (
            item
            for item in PushSubscription.objects.filter(user=request.user, enabled=True)
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
