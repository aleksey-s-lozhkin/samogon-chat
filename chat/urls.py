from django.urls import path

from . import views


app_name = "chat"

urlpatterns = [
    path(
        "attachments/<uuid:attachment_id>/",
        views.serve_attachment,
        name="attachment",
    ),
    path(
        "attachments/<uuid:attachment_id>/download/",
        views.serve_attachment,
        name="download_attachment",
    ),
    path(
        "messages/<int:message_id>/attachments/",
        views.add_message_attachments,
        name="message_attachments",
    ),
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
