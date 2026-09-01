from django.contrib.auth import authenticate, login, logout
from django.contrib.auth.decorators import login_required
from django.conf import settings
from django.http import HttpResponse, JsonResponse
from django.shortcuts import redirect, render
from django.views.decorators.http import require_POST

from config.rate_limit import request_is_allowed
from chat.services.navigation import get_last_room_url

from .forms import ProfileForm, RegistrationForm
from .turnstile import verify_turnstile


def is_htmx_request(request):
    """Определяет, ожидает ли браузер HTML-фрагмент вместо JSON."""
    return request.headers.get("HX-Request") == "true"


def htmx_error(request, message, status=200):
    return render(
        request,
        "chat/partials/auth_error.html",
        {"message": message},
        status=status,
    )


def registration_error_message(form):
    errors = [
        message
        for field_errors in form.errors.values()
        for message in field_errors
    ]
    return " ".join(errors)


def rate_limit_error(request, message):
    """Возвращает понятную ошибку, не раскрывая детали лимита."""
    if is_htmx_request(request):
        return htmx_error(request, message, status=429)

    return JsonResponse({"success": False, "error": message}, status=429)


def authentication_error(request, message, status=400):
    """Возвращает ошибку формы в формате, который ждёт текущий клиент."""
    if is_htmx_request(request):
        return htmx_error(request, message, status=status)
    return JsonResponse({"success": False, "error": message}, status=status)


@require_POST
def login_view(request):
    if not request_is_allowed(
        request,
        bucket="login",
        limit=settings.LOGIN_RATE_LIMIT,
    ):
        return rate_limit_error(
            request,
            "Слишком много попыток входа. Подождите минуту.",
        )

    username = request.POST.get("username", "").strip()
    password = request.POST.get("password", "")

    user = authenticate(
        request,
        username=username,
        password=password,
    )

    if user is None:
        if is_htmx_request(request):
            return htmx_error(request, "Неверный логин или пароль")

        return JsonResponse(
            {
                "success": False,
                "error": "Неверный логин или пароль",
            },
            status=400,
        )

    if user.is_banned:
        return authentication_error(
            request,
            "Этот аккаунт временно недоступен. Обратитесь к модератору.",
            status=403,
        )

    login(request, user)

    if is_htmx_request(request):
        return HttpResponse(headers={"HX-Refresh": "true"})

    return JsonResponse(
        {
            "success": True,
            "username": user.username,
        }
    )


@require_POST
def logout_view(request):
    """Завершает пользовательскую сессию."""

    logout(request)
    return redirect("home")


@require_POST
def register_view(request):
    """Регистрирует нового пользователя."""
    if not request_is_allowed(
        request,
        bucket="registration",
        limit=settings.REGISTRATION_RATE_LIMIT,
    ):
        return rate_limit_error(
            request,
            "Регистрация временно слишком занята. Подождите минуту.",
        )

    form = RegistrationForm(request.POST)

    if not verify_turnstile(
        request.POST.get("cf-turnstile-response", ""),
        request.META.get("HTTP_X_REAL_IP") or request.META.get("REMOTE_ADDR"),
    ):
        return authentication_error(
            request,
            "Не удалось подтвердить, что вы человек. Попробуйте ещё раз.",
            status=400,
        )

    if not form.is_valid():
        if is_htmx_request(request):
            return htmx_error(request, registration_error_message(form))

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

    if is_htmx_request(request):
        return HttpResponse(headers={"HX-Refresh": "true"})

    return JsonResponse(
        {
            "success": True,
            "username": user.username,
        }
    )


@login_required
def profile(request):
    """Отображает и изменяет профиль пользователя."""

    if request.method == "POST":
        form = ProfileForm(
            request.POST,
            request.FILES,
            instance=request.user,
        )

        if form.is_valid():
            try:
                form.save()
            except OSError:
                # Хранилище может быть недоступно из контейнера или без прав.
                form.add_error(
                    "avatar",
                    "Не удалось сохранить изображение. Попробуйте ещё раз.",
                )
            else:
                return redirect("profile")

    else:
        form = ProfileForm(
            instance=request.user,
        )

    return render(
        request,
        "users/profile.html",
        {
            "form": form,
            "last_room_url": get_last_room_url(request),
            "has_last_room": bool(request.session.get("last_chat_room_slug")),
        },
    )
