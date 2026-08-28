from django.urls import path

from .views import chat_page, rooms_page


app_name = "chat"

urlpatterns = [
    path("", rooms_page, name="rooms"),
    path("<slug:room_slug>/", chat_page, name="chat"),
]