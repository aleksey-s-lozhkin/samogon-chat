from django.contrib.auth import views as auth_views
from django.urls import path, reverse_lazy

from .views import (
    ComfortablePasswordResetView,
    disconnect_social_account,
    login_view,
    logout_view,
    profile,
    push_subscribe,
    push_status,
    push_unsubscribe,
    register_view,
)


urlpatterns = [
    path("login/", login_view, name="login"),
    path("logout/", logout_view, name="logout"),
    path("register/", register_view, name="register"),
    path(
        "password-reset/",
        ComfortablePasswordResetView.as_view(),
        name="password_reset",
    ),
    path(
        "password-reset/sent/",
        auth_views.PasswordResetDoneView.as_view(
            template_name="users/password_reset_done.html",
        ),
        name="password_reset_done",
    ),
    path(
        "password-reset/<uidb64>/<token>/",
        auth_views.PasswordResetConfirmView.as_view(
            template_name="users/password_reset_confirm.html",
            success_url=reverse_lazy("password_reset_complete"),
        ),
        name="password_reset_confirm",
    ),
    path(
        "password-reset/complete/",
        auth_views.PasswordResetCompleteView.as_view(
            template_name="users/password_reset_complete.html",
        ),
        name="password_reset_complete",
    ),
    path("profile/", profile, name="profile"),
    path("profile/push/subscribe/", push_subscribe, name="push_subscribe"),
    path("profile/push/status/", push_status, name="push_status"),
    path("profile/push/unsubscribe/", push_unsubscribe, name="push_unsubscribe"),
    path(
        "profile/connections/<str:provider>/disconnect/",
        disconnect_social_account,
        name="disconnect_social_account",
    ),
]
