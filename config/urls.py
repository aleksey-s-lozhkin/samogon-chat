from django.contrib import admin
from django.urls import path

from chat.views import chat_page


urlpatterns = [
    path('admin/', admin.site.urls),
    path("chat/<str:room_name>/", chat_page, name="chat")
]
