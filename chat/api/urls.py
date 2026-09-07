from django.urls import path

from .views import (
    api_message_attachments,
    api_bartender_job,
    api_bartender_jobs,
    api_message_reactions,
    api_message_report,
    api_note_detail,
    api_notes,
    api_room_messages,
    api_rooms,
)


urlpatterns = [
    path("rooms/", api_rooms, name="api_v1_chat_rooms"),
    path("notes/", api_notes, name="api_v1_chat_notes"),
    path("notes/<int:note_id>/", api_note_detail, name="api_v1_chat_note_detail"),
    path(
        "rooms/<slug:room_slug>/bartender/jobs/",
        api_bartender_jobs,
        name="api_v1_bartender_jobs",
    ),
    path(
        "bartender/jobs/<uuid:job_id>/",
        api_bartender_job,
        name="api_v1_bartender_job",
    ),
    path("rooms/<slug:room_slug>/messages/", api_room_messages, name="api_v1_chat_messages"),
    path(
        "rooms/<slug:room_slug>/messages/<int:message_id>/reactions/",
        api_message_reactions,
        name="api_v1_chat_message_reactions",
    ),
    path(
        "rooms/<slug:room_slug>/messages/<int:message_id>/reports/",
        api_message_report,
        name="api_v1_chat_message_report",
    ),
    path(
        "rooms/<slug:room_slug>/messages/<int:message_id>/attachments/",
        api_message_attachments,
        name="api_v1_chat_message_attachments",
    ),
]
