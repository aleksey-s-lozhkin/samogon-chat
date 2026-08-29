from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.db import transaction
from django.db.models import Q
from django.http import Http404
from django.shortcuts import get_object_or_404, redirect, render
from django.utils.text import slugify

from .forms import PrivateRoomForm
from .models import Room, RoomMembership
from .services.messages import MessageService


def get_visible_rooms(user):
    """Возвращает открытые комнаты и личные столики текущего гостя."""
    rooms = Room.objects.filter(visibility=Room.Visibility.PUBLIC)
    if user.is_authenticated:
        rooms = Room.objects.filter(
            Q(visibility=Room.Visibility.PUBLIC)
            | Q(memberships__user=user),
        ).distinct()
    return rooms.order_by("visibility", "name")


def add_unread_counts(rooms, user):
    """Добавляет в объекты комнат число непрочитанных сообщений."""
    for room in rooms:
        room.unread_count = (
            MessageService.get_unread_count(room=room, user_id=user.id)
            if user.is_authenticated
            else 0
        )
    return rooms


def private_room_slug(name):
    """Создаёт уникальный URL для тайного столика."""
    base_slug = slugify(name) or "taynyy-stolik"
    slug = base_slug
    number = 2
    while Room.objects.filter(slug=slug).exists():
        slug = f"{base_slug}-{number}"
        number += 1
    return slug


def rooms_page(request):
    rooms = list(get_visible_rooms(request.user))
    add_unread_counts(rooms, request.user)
    public_rooms = [room for room in rooms if not room.is_private]
    private_rooms = [room for room in rooms if room.is_private]
    owned_private_room = next(
        (room for room in private_rooms if room.owner_id == request.user.id),
        None,
    ) if request.user.is_authenticated else None

    return render(
        request,
        "chat/rooms.html",
        {
            "rooms": public_rooms,
            "private_rooms": private_rooms,
            "owned_private_room": owned_private_room,
            "private_room_form": PrivateRoomForm(user=request.user),
        },
    )


def chat_page(request, room_slug):
    room = get_object_or_404(
        Room,
        slug=room_slug,
    )

    if room.is_private and (
        not request.user.is_authenticated
        or not room.memberships.filter(user=request.user).exists()
    ):
        raise Http404("Тайный столик не найден")

    if request.user.is_authenticated:
        MessageService.mark_room_as_read(room=room, user_id=request.user.id)
    rooms = list(get_visible_rooms(request.user))
    add_unread_counts(rooms, request.user)

    return render(
        request,
        "chat/chat.html",
        {
            "room": room,
            "rooms": rooms,
            "public_rooms": [item for item in rooms if not item.is_private],
            "private_rooms": [item for item in rooms if item.is_private],
        },
    )


@login_required
def create_private_room(request):
    """Создаёт один личный столик владельца и приглашает до двух гостей."""
    if request.method != "POST":
        return redirect("chat:rooms")

    if Room.objects.filter(
        owner=request.user,
        visibility=Room.Visibility.PRIVATE,
    ).exists():
        messages.error(request, "У вас уже есть свой тайный столик.")
        return redirect("chat:rooms")

    form = PrivateRoomForm(request.POST, user=request.user)
    if not form.is_valid():
        rooms = list(get_visible_rooms(request.user))
        add_unread_counts(rooms, request.user)
        return render(
            request,
            "chat/rooms.html",
            {
                "rooms": [room for room in rooms if not room.is_private],
                "private_rooms": [room for room in rooms if room.is_private],
                "owned_private_room": None,
                "private_room_form": form,
            },
            status=400,
        )

    with transaction.atomic():
        room = Room.objects.create(
            name=form.cleaned_data["name"],
            slug=private_room_slug(form.cleaned_data["name"]),
            description="Тайный столик: разговор остаётся между своими.",
            visibility=Room.Visibility.PRIVATE,
            owner=request.user,
        )
        RoomMembership.objects.bulk_create(
            [
                RoomMembership(room=room, user=user)
                for user in (request.user, *form.cleaned_data["members"])
            ]
        )

    messages.success(request, "Тайный столик готов. Гости уже в списке.")
    return redirect("chat:chat", room_slug=room.slug)
