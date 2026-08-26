

from django.urls import path

from .views import chat_page, login_view, logout_view, register_view

urlpatterns = [
    path("login/", login_view, name="login"),
    path("logout/", logout_view, name="logout"),
    path("register/", register_view, name="register"),
    path("<str:room_name>/", chat_page, name="chat"),
]