from django.shortcuts import render


def home(request):
    """Стартовая страница."""

    return render(
        request,
        "home.html",
    )
