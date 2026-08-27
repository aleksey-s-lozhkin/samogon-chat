from django.shortcuts import render


def chat_page(request, room_name):
    return render(
        request,
        "chat/chat.html",
        {
            "room_name": room_name,
        },
    )
