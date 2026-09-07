from django.urls import path

from .views import api_room_messages, api_rooms


urlpatterns = [
    path("rooms/", api_rooms, name="api_v1_chat_rooms"),
    path("rooms/<slug:room_slug>/messages/", api_room_messages, name="api_v1_chat_messages"),
]
