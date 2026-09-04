from django.conf.urls.static import static
from django.contrib import admin
from django.urls import include, path

from config import settings
from config.views import home, offline, pwa_manifest, robots, service_worker

urlpatterns = [
    path("admin/", admin.site.urls),
    path("accounts/", include("allauth.urls")),
    path("chat/", include("chat.urls")),
    path("users/", include("users.urls")),
    path("robots.txt", robots, name="robots"),
    path("manifest.webmanifest", pwa_manifest, name="pwa_manifest"),
    path("service-worker.js", service_worker, name="service_worker"),
    path("offline/", offline, name="offline"),
    path("", home, name="home"),
]

if settings.DEBUG:
    urlpatterns += static(
        settings.MEDIA_URL,
        document_root=settings.MEDIA_ROOT,
    )
