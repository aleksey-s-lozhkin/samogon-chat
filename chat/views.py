from django.shortcuts import render, get_object_or_404

from .models import Room


def chat_page(request, room_slug):
    room = get_object_or_404(Room, slug=room_slug)

    return render(
        request,
        "chat/chat.html",
        {
            "room": room,
        },
    )

def rooms_page(request):
    rooms = Room.objects.order_by("name")

    return render(
        request,
        "chat/rooms.html",
        {
            "rooms": rooms,
        },
    )
