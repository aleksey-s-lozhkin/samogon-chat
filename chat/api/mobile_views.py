from asgiref.sync import async_to_sync
from django.http import JsonResponse, HttpResponse
from django.views.decorators.cache import never_cache
from users.services.safety import blocked_ids
from drf_spectacular.utils import extend_schema, extend_schema_view, OpenApiParameter
from rest_framework.decorators import api_view
from chat.services import private_rooms
from chat.services.guests import eligible_guests, guest_data
from chat.services.presence import online_users
from users.models import ChatStatus
from .mobile_serializers import (MobileErrorSerializer, RoomDetailSerializer, RoomWriteSerializer, GuestsSerializer, StatusListSerializer)


def error_response(error):
    return JsonResponse({"error": error.code, "fields": error.fields}, status=error.status)


def detail_data(room, user):
    from .views import room_data
    account = lambda u: {"id": u.pk, "username": u.username, "display_name": u.username}
    owner = room.is_private and room.owner_id == user.pk
    return {**room_data(room, user),
        "owner": account(room.owner) if room.is_private else None,
        "members": [account(u) for u in room.members.all()] if room.is_private else [],
        "can_manage": bool(owner), "can_delete": bool(owner),
        "can_leave": room.is_private and not owner,
    }


def create_room_response(request):
    serializer = RoomWriteSerializer(data=request.data)
    if not serializer.is_valid():
        return JsonResponse({"error": "invalid_room", "fields": serializer.errors}, status=400)
    data = serializer.validated_data
    try:
        room = private_rooms.create_private_room(actor=request.user, data={"name": data["name"], "members": data["member_ids"]})
    except private_rooms.RoomOperationError as error:
        return error_response(error)
    return JsonResponse(detail_data(room, request.user), status=201)


errors = {status: MobileErrorSerializer for status in (400, 401, 403, 404, 409)}


@never_cache
@extend_schema_view(
    get=extend_schema(operation_id="api_v1_chat_room_detail", tags=["chat"], auth=[{"cookieAuth": []}], responses={200: RoomDetailSerializer, **errors}),
    patch=extend_schema(tags=["chat"], auth=[{"cookieAuth": []}], request=RoomWriteSerializer, responses={200: RoomDetailSerializer, **errors}),
    delete=extend_schema(tags=["chat"], auth=[{"cookieAuth": []}], responses={204: None, **errors}),
)
@api_view(["GET", "PATCH", "DELETE"])
def api_room_detail(request, room_slug):
    from .views import auth_error, accessible_room
    if error := auth_error(request): return error
    room = accessible_room(request.user, room_slug)
    if room is None: return JsonResponse({"error": "room_not_found"}, status=404)
    if request.method == "GET": return JsonResponse(detail_data(room, request.user))
    if not room.is_private or room.owner_id != request.user.pk:
        return JsonResponse({"error": "room_management_forbidden"}, status=403)
    try:
        if request.method == "DELETE":
            private_rooms.delete_private_room(actor=request.user, room_id=room.pk)
            if request.session.get("last_chat_room_slug") == room_slug:
                request.session.pop("last_chat_room_slug", None)
            return HttpResponse(status=204)
        serializer = RoomWriteSerializer(data=request.data, partial=True)
        if not serializer.is_valid():
            return JsonResponse({"error": "invalid_room", "fields": serializer.errors}, status=400)
        data = serializer.validated_data
        values = {}
        if "name" in data: values["name"] = data["name"]
        if "member_ids" in data: values["members"] = data["member_ids"]
        room = private_rooms.update_private_room(actor=request.user, room_id=room.pk, data=values)
    except private_rooms.RoomOperationError as error:
        return error_response(error)
    return JsonResponse(detail_data(room, request.user))


@never_cache
@extend_schema(tags=["chat"], auth=[{"cookieAuth": []}], request=None, responses={204: None, **errors})
@api_view(["POST"])
def api_room_leave(request, room_slug):
    from .views import auth_error, accessible_room
    if error := auth_error(request): return error
    room = accessible_room(request.user, room_slug)
    if room is None: return JsonResponse({"error": "room_not_found"}, status=404)
    try:
        private_rooms.leave_private_room(actor=request.user, room_id=room.pk)
    except private_rooms.RoomOperationError as error:
        return error_response(error)
    if request.session.get("last_chat_room_slug") == room_slug:
        request.session.pop("last_chat_room_slug", None)
    return HttpResponse(status=204)


@never_cache
@extend_schema(tags=["chat"], auth=[{"cookieAuth": []}], parameters=[OpenApiParameter("offset", int), OpenApiParameter("limit", int), OpenApiParameter("q", str)], responses={200: GuestsSerializer, **errors})
@api_view(["GET"])
def api_guests(request):
    from .views import auth_error
    if error := auth_error(request): return error
    try:
        offset, limit = int(request.query_params.get("offset", 0)), int(request.query_params.get("limit", 50))
        if offset < 0 or not 1 <= limit <= 100: raise ValueError
    except (TypeError, ValueError): return JsonResponse({"error": "invalid_pagination"}, status=400)
    query = request.query_params.get("q", "").strip()
    if len(query) > 50: return JsonResponse({"error": "invalid_query"}, status=400)
    users = eligible_guests().exclude(pk__in=blocked_ids(request.user.pk)).order_by("username", "id")
    if query: users = users.filter(username__icontains=query)
    page = list(users[offset:offset + limit + 1])
    labels = dict(ChatStatus.objects.values_list("code", "label"))
    names = async_to_sync(online_users.get_all_users)()
    online = list(users.filter(username__in=names).values_list("username", flat=True))
    more = len(page) > limit
    return JsonResponse({"api_version": "v1", "guests": [guest_data(u, labels) for u in page[:limit]], "online": online, "has_more": more, "next_offset": offset + limit if more else None})


@never_cache
@extend_schema(tags=["users"], auth=[{"cookieAuth": []}], responses={200: StatusListSerializer, **errors})
@api_view(["GET"])
def api_statuses(request):
    from .views import auth_error
    if error := auth_error(request): return error
    return JsonResponse({"api_version": "v1", "statuses": list(ChatStatus.objects.filter(is_active=True).values("code", "label"))})
