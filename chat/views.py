from django.contrib.auth import authenticate, login, logout
from django.http import JsonResponse
from django.shortcuts import render
from django.views.decorators.http import require_POST
from .forms import RegistrationForm


def chat_page(request, room_name):
    return render(
        request,
        "chat/chat.html",
        {
            "room_name": room_name,
        },
    )


@require_POST
def login_view(request):
    username = request.POST.get("username", "").strip()
    password = request.POST.get("password", "")

    user = authenticate(
        request,
        username=username,
        password=password,
    )

    if user is None:
        return JsonResponse(
            {
                "success": False,
                "error": "Неверный логин или пароль",
            },
            status=400,
        )

    login(request, user)

    return JsonResponse(
        {
            "success": True,
            "username": user.username,
        }
    )

@require_POST
def logout_view(request):
    """Выход пользователя из аккаунта."""
    logout(request)

    return JsonResponse({
        "success": True,
    })

@require_POST
def register_view(request):
    """Регистрирует нового пользователя."""

    form = RegistrationForm(request.POST)

    if not form.is_valid():
        errors = {}

        for field, messages in form.errors.items():
            errors[field] = messages.get_json_data()

        return JsonResponse(
            {
                "success": False,
                "errors": errors,
            },
            status=400,
        )

    user = form.save()

    login(request, user)

    return JsonResponse(
        {
            "success": True,
            "username": user.username,
        }
    )