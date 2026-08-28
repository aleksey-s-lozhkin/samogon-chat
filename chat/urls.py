from django.urls import path

from .views import chat_page


urlpatterns = [
    path("<slug:room_name>/", chat_page, name="chat"),
]