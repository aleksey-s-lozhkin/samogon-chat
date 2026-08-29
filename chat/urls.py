from django.urls import path

from . import views


app_name = "chat"

urlpatterns = [
    path(
        "private-rooms/create/",
        views.create_private_room,
        name="create_private_room",
    ),
    path(
        "",
        views.rooms_page,
        name="rooms",
    ),
    path(
        "<slug:room_slug>/",
        views.chat_page,
        name="chat",
    ),
]
