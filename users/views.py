from django.contrib.auth import authenticate, login, logout
from django.contrib.auth.decorators import login_required
from django.http import HttpResponse, JsonResponse
from django.shortcuts import render, redirect
from django.views.decorators.http import require_POST
from .forms import RegistrationForm, ProfileForm


def is_htmx_request(request):
    return request.headers.get("HX-Request") == "true"


def htmx_error(request, message):
    return render(
        request,
        "chat/partials/auth_error.html",
        {"message": message},
    )


def registration_error_message(form):
    errors = [
        message
        for field_errors in form.errors.values()
        for message in field_errors
    ]
    return " ".join(errors)


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
        if is_htmx_request(request):
            return htmx_error(request, "Неверный логин или пароль")

        return JsonResponse(
            {
                "success": False,
                "error": "Неверный логин или пароль",
            },
            status=400,
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
    """Выход пользователя из системы."""

    if request.method == "POST":
        logout(request)

    return redirect("home")

@require_POST
def register_view(request):
    """Регистрирует нового пользователя."""

    form = RegistrationForm(request.POST)

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
            form.save()
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
        },
    )
