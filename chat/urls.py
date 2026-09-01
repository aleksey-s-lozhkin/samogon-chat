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
        "notes/attachments/<uuid:attachment_id>/",
        views.serve_note_attachment,
        name="note_attachment",
    ),
    path(
        "notes/attachments/<uuid:attachment_id>/download/",
        views.serve_note_attachment,
        name="download_note_attachment",
    ),
    path(
        "messages/<int:message_id>/attachments/",
        views.add_message_attachments,
        name="message_attachments",
    ),
    path(
        "messages/<int:message_id>/delete/",
        views.delete_message,
        name="delete_message",
    ),
    path("notes/", views.notes_page, name="notes"),
    path("notes/create/", views.create_note, name="create_note"),
    path("notes/<int:note_id>/delete/", views.delete_note, name="delete_note"),
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
