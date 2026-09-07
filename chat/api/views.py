from asgiref.sync import async_to_sync
from channels.layers import get_channel_layer
from django.conf import settings
from django.contrib.auth import get_user_model
from django.http import JsonResponse
from drf_spectacular.utils import OpenApiParameter, extend_schema, extend_schema_view
from rest_framework.decorators import api_view

from chat.models import Message, Room
from chat.selectors import get_visible_rooms
from chat.services.messages import MessageService
from chat.validators import MESSAGE_MAX_LENGTH
from config.rate_limit import is_allowed
from users.services.push import send_direct_message_push

from .serializers import (
    ChatApiErrorSerializer,
    MessageCreateSerializer,
    MessageSerializer,
    MessagesResponseSerializer,
    RoomsResponseSerializer,
)


User = get_user_model()


def auth_error(request):
    if not request.user.is_authenticated:
        return JsonResponse({"error": "authentication_required"}, status=401)
    if request.user.is_banned:
        return JsonResponse({"error": "account_unavailable"}, status=403)
    return None


def visible_rooms(user):
    return get_visible_rooms(user)


def accessible_room(user, slug):
    return visible_rooms(user).filter(slug=slug).first()


def room_data(room, user):
    return {
        "slug": room.slug,
        "name": room.name,
        "description": room.description,
        "visibility": room.visibility,
        "unread_count": MessageService.get_unread_count(room=room, user_id=user.id),
    }


@extend_schema(tags=("chat",), auth=({"cookieAuth": []},), responses={200: RoomsResponseSerializer, 401: ChatApiErrorSerializer, 403: ChatApiErrorSerializer})
@api_view(("GET",))
def api_rooms(request):
    if error := auth_error(request):
        return error
    rooms = list(visible_rooms(request.user))
    return JsonResponse({"api_version": "v1", "rooms": [room_data(room, request.user) for room in rooms]})


@extend_schema_view(
    get=extend_schema(
        tags=("chat",),
        auth=({"cookieAuth": []},),
        parameters=[OpenApiParameter("limit", int, required=False, description="1–100, default 50")],
        responses={200: MessagesResponseSerializer, 400: ChatApiErrorSerializer, 401: ChatApiErrorSerializer, 403: ChatApiErrorSerializer, 404: ChatApiErrorSerializer},
    ),
    post=extend_schema(
        tags=("chat",),
        auth=({"cookieAuth": []},),
        request=MessageCreateSerializer,
        responses={201: MessageSerializer, 400: ChatApiErrorSerializer, 401: ChatApiErrorSerializer, 403: ChatApiErrorSerializer, 404: ChatApiErrorSerializer, 429: ChatApiErrorSerializer},
    ),
)
@api_view(("GET", "POST"))
def api_room_messages(request, room_slug):
    if error := auth_error(request):
        return error
    room = accessible_room(request.user, room_slug)
    if room is None:
        return JsonResponse({"error": "room_not_found"}, status=404)
    if request.method == "GET":
        try:
            limit = int(request.GET.get("limit", 50))
        except (TypeError, ValueError):
            return JsonResponse({"error": "invalid_limit"}, status=400)
        if not 1 <= limit <= 100:
            return JsonResponse({"error": "invalid_limit"}, status=400)
        MessageService.mark_room_as_read(room=room, user_id=request.user.id)
        return JsonResponse({"api_version": "v1", "room": room_data(room, request.user), "messages": MessageService.get_room_messages(room, viewer_id=request.user.id, limit=limit)})

    if not is_allowed(
        identifier=f"user:{request.user.id}",
        bucket="message",
        limit=settings.MESSAGE_RATE_LIMIT,
        window_seconds=settings.RATE_LIMIT_WINDOW_SECONDS,
    ):
        return JsonResponse({"error": "rate_limited"}, status=429)
    serializer = MessageCreateSerializer(data=request.data)
    if not serializer.is_valid():
        return JsonResponse({"error": "invalid_request"}, status=400)
    text = serializer.validated_data["message"].strip()
    if not text or len(text) > MESSAGE_MAX_LENGTH:
        return JsonResponse({"error": "invalid_message"}, status=400)
    recipient = None
    recipient_name = serializer.validated_data.get("recipient")
    if recipient_name:
        recipient = User.objects.filter(username=recipient_name, is_active=True, is_superuser=False).first()
        if recipient is None or recipient.id == request.user.id or recipient.is_banned:
            return JsonResponse({"error": "recipient_not_found"}, status=404)
        if room.is_private and not room.memberships.filter(user=recipient).exists():
            return JsonResponse({"error": "recipient_not_found"}, status=404)
    reply_to = None
    reply_to_id = serializer.validated_data.get("reply_to")
    if reply_to_id:
        reply_to = Message.objects.select_related("room", "user", "recipient").filter(pk=reply_to_id, room=room).first()
        if reply_to is None or not MessageService.can_view_message(message=reply_to, user=request.user):
            return JsonResponse({"error": "reply_not_found"}, status=404)
        if reply_to.recipient_id:
            other_id = reply_to.recipient_id if reply_to.user_id == request.user.id else reply_to.user_id
            recipient = User.objects.filter(pk=other_id).first()
    message = MessageService.create_message(user_id=request.user.id, room=room, text=text, recipient_id=recipient.id if recipient else None, reply_to_id=reply_to.id if reply_to else None)
    payload = MessageService.serialize_message(message, viewer_id=request.user.id)
    event = {"type": "direct_message" if recipient else "chat_message", **payload, "timestamp": payload["created_at"], "room_slug": room.slug, "room_private": room.is_private}
    channel_layer = get_channel_layer()
    groups = [f"chat_user_{request.user.id}", f"chat_user_{recipient.id}"] if recipient else ([f"chat_user_{user_id}" for user_id in room.memberships.values_list("user_id", flat=True)] if room.is_private else [f"chat_{room.slug}"])
    for group in set(groups):
        async_to_sync(channel_layer.group_send)(group, event)
    if recipient and recipient.username != settings.BARTENDER_USERNAME:
        send_direct_message_push(recipient_id=recipient.id, room_slug=room.slug)
    return JsonResponse(payload, status=201)
