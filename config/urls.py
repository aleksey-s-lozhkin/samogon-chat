from django.conf.urls.static import static
from django.contrib import admin
from django.urls import include, path

from config import settings
from config.views import home, robots

urlpatterns = [
    path("admin/", admin.site.urls),
    path("chat/", include("chat.urls")),
    path("users/", include("users.urls")),
    path("robots.txt", robots, name="robots"),
    path("", home, name="home"),
]

if settings.DEBUG:
    urlpatterns += static(
        settings.MEDIA_URL,
        document_root=settings.MEDIA_ROOT,
    )
