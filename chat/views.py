from django.shortcuts import get_object_or_404, render

from .models import Room


def rooms_page(request):
    rooms = Room.objects.all()

    return render(
        request,
        "chat/rooms.html",
        {
            "rooms": rooms,
        },
    )


def chat_page(request, room_slug):
    room = get_object_or_404(
        Room,
        slug=room_slug,
    )

    rooms = Room.objects.all()

    return render(
        request,
        "chat/chat.html",
        {
            "room": room,
            "rooms": rooms,
        },
    )