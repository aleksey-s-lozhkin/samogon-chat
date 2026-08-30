from django.contrib.auth import logout
from django.shortcuts import redirect


class BannedUserMiddleware:
    """Закрывает HTTP-сессию заблокированного пользователя на первом запросе."""

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        user = getattr(request, "user", None)
        if user and user.is_authenticated and user.is_banned:
            logout(request)
            return redirect("home")
        return self.get_response(request)
