from django.http import HttpResponse
from django.shortcuts import render


def home(request):
    """Стартовая страница."""

    return render(
        request,
        "home.html",
    )


def robots(request):
    """Не индексирует закрытую бета-версию и убирает лишнее предупреждение."""
    return HttpResponse(
        "User-agent: *\nDisallow: /\n",
        content_type="text/plain",
    )
