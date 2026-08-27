from django.urls import path

from .views import chat_page


urlpatterns = [
    path("<str:room_name>/", chat_page, name="chat"),
]