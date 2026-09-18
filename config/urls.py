from django.conf.urls.static import static
from django.contrib import admin
from django.urls import include, path
from drf_spectacular.views import SpectacularAPIView, SpectacularSwaggerView

from users.views import auth_entry, ComfortablePasswordResetView
from django.contrib.auth import views as auth_views

from config import settings
from config.views import (
    health_live,
    health_ready,
    home,
    offline,
    pwa_manifest,
    robots,
    service_rules,
    service_worker,
)

urlpatterns = [
    path("health/live/", health_live, name="health_live"),
    path("health/ready/", health_ready, name="health_ready"),
    path("admin/", admin.site.urls),
    path("api/schema/", SpectacularAPIView.as_view(), name="api_schema"),
    path(
        "api/docs/",
        SpectacularSwaggerView.as_view(url_name="api_schema"),
        name="api_docs",
    ),
    path("api/v1/", include("users.api.urls")),
    path("api/v1/chat/", include("chat.api.urls")),
    path("accounts/login/", auth_entry, name="account_login"),
    path("accounts/signup/", auth_entry, {"mode": "register"}, name="account_signup"),
    path("accounts/password/reset/", ComfortablePasswordResetView.as_view(), name="account_reset_password"),
    path("accounts/password/reset/done/", auth_views.PasswordResetDoneView.as_view(
        template_name="users/password_reset_done.html",
    )),
    path("accounts/", include("allauth.urls")),
    path("chat/", include("chat.urls")),
    path("users/", include("users.urls")),
    path("robots.txt", robots, name="robots"),
    path("manifest.webmanifest", pwa_manifest, name="pwa_manifest"),
    path("service-worker.js", service_worker, name="service_worker"),
    path("offline/", offline, name="offline"),
    path("rules/", service_rules, name="service_rules"),
    path("", home, name="home"),
]

if settings.DEBUG:
    urlpatterns += static(
        settings.MEDIA_URL,
        document_root=settings.MEDIA_ROOT,
    )
