import uuid
from asgiref.sync import async_to_sync
from channels.layers import get_channel_layer
from django.db import transaction
from django.utils.text import slugify
from chat.forms import PrivateRoomForm
from chat.models import Room, RoomMembership
from users.models import User


class RoomOperationError(Exception):
    def __init__(self, code, status=400, fields=None):
        self.code, self.status, self.fields = code, status, fields or {}
        super().__init__(code)


def revoke_private_room_access(*, room_slug, user_ids):
    for user_id in user_ids:
        async_to_sync(get_channel_layer().group_send)(
            f"chat_user_{user_id}", {"type": "room_access_revoked", "room_slug": room_slug},
        )


def validate(actor, data, room=None):
    form = PrivateRoomForm(data, user=actor, room=room)
    if not form.is_valid():
        raise RoomOperationError("invalid_room", fields=form.errors.get_json_data())
    return form.cleaned_data


@transaction.atomic
def create_private_room(*, actor, data):
    User.objects.select_for_update().get(pk=actor.pk)
    if Room.objects.filter(owner=actor, visibility=Room.Visibility.PRIVATE).exists():
        raise RoomOperationError("owned_room_exists", 409)
    values = validate(actor, data)
    room = Room.objects.create(
        name=values["name"], slug=f"{slugify(values['name'])[:65] or 'private'}-{uuid.uuid4().hex[:12]}",
        description="Закрытая беседа доступна только её участникам.",
        visibility=Room.Visibility.PRIVATE, owner=actor,
    )
    RoomMembership.objects.bulk_create([RoomMembership(room=room, user=u) for u in (actor, *values["members"])])
    return room


def locked_room(actor, room_id, owner=False):
    room = Room.objects.select_for_update().filter(pk=room_id, visibility=Room.Visibility.PRIVATE).first()
    if not room or not room.memberships.filter(user=actor).exists():
        raise RoomOperationError("room_not_found", 404)
    if owner and room.owner_id != actor.pk:
        raise RoomOperationError("room_management_forbidden", 403)
    return room


@transaction.atomic
def update_private_room(*, actor, room_id, data):
    room = locked_room(actor, room_id, owner=True)
    if hasattr(data, "getlist"):
        data = {"name": data.get("name", ""), "members": data.getlist("members")}
    data = {"name": room.name, "members": list(room.members.exclude(pk=actor.pk).values_list("pk", flat=True)), **data}
    values = validate(actor, data, room)
    previous = set(room.members.values_list("id", flat=True))
    room.name = values["name"]
    room.save(update_fields=("name",))
    members = (actor, *values["members"])
    room.members.set(members)
    removed = previous - {u.pk for u in members}
    transaction.on_commit(lambda: revoke_private_room_access(room_slug=room.slug, user_ids=removed))
    return room


@transaction.atomic
def delete_private_room(*, actor, room_id):
    room = locked_room(actor, room_id, owner=True)
    slug, ids = room.slug, list(room.members.values_list("id", flat=True))
    room.delete()
    transaction.on_commit(lambda: revoke_private_room_access(room_slug=slug, user_ids=ids))


@transaction.atomic
def leave_private_room(*, actor, room_id):
    room = locked_room(actor, room_id)
    if room.owner_id == actor.pk:
        raise RoomOperationError("owner_cannot_leave", 409)
    room.memberships.filter(user=actor).delete()
    transaction.on_commit(lambda: revoke_private_room_access(room_slug=room.slug, user_ids=(actor.pk,)))
