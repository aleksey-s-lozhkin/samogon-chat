import json
from urllib.parse import urlparse

from django.contrib.auth import authenticate, get_user_model, login, logout
from django.contrib.auth.decorators import login_required
from django.contrib.auth.views import PasswordResetView
from django.contrib import messages
from django.conf import settings
from django.http import HttpResponse, JsonResponse
from django.shortcuts import redirect, render
from django.urls import reverse, reverse_lazy
from django.utils.http import url_has_allowed_host_and_scheme
from django.views.decorators.http import require_GET, require_POST
from allauth.socialaccount.models import SocialAccount

from config.rate_limit import request_is_allowed
from chat.services.navigation import get_last_room_url

from .forms import ProfileForm, RegistrationForm
from .models import PushSubscription
from .turnstile import verify_turnstile

User = get_user_model()


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


def registration_form_error(request, form):
    """Возвращает общую и привязанные к полям ошибки регистрации."""
    return render(
        request,
        "chat/partials/auth_error.html",
        {
            "message": registration_error_message(form),
            "field_errors": {
                field: " ".join(form.errors.get(field, ()))
                for field in ("username", "email", "invite_code", "password")
            },
            "error_prefix": "register-error",
        },
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


def get_safe_return_url(request):
    """Возвращает только локальный адрес, переданный формой авторизации."""
    return_url = request.POST.get("next", "").strip()
    if return_url and url_has_allowed_host_and_scheme(
        return_url,
        allowed_hosts={request.get_host()},
        require_https=request.is_secure(),
    ):
        return return_url
    return reverse("home")


def find_login_username(identifier):
    """Находит имя пользователя по логину или уникальному email."""
    username_field = User.USERNAME_FIELD
    users = User.objects.filter(**{f"{username_field}__iexact": identifier})
    if users.count() == 1:
        return getattr(users.first(), username_field)

    users = User.objects.filter(email__iexact=identifier)
    if users.count() == 1:
        return getattr(users.first(), username_field)
    return identifier


class ComfortablePasswordResetView(PasswordResetView):
    """Не раскрывает наличие email и ограничивает отправку писем по IP."""

    template_name = "users/password_reset_form.html"
    email_template_name = "users/password_reset_email.txt"
    subject_template_name = "users/password_reset_subject.txt"
    success_url = reverse_lazy("password_reset_done")

    def post(self, request, *args, **kwargs):
        if not request_is_allowed(
            request,
            bucket="password-reset",
            limit=settings.PASSWORD_RESET_RATE_LIMIT,
        ):
            form = self.get_form()
            form.add_error(
                None,
                "Слишком много запросов. Подождите минуту и попробуйте снова.",
            )
            return self.render_to_response(
                self.get_context_data(form=form),
                status=429,
            )
        return super().post(request, *args, **kwargs)


def authentication_success(request, user):
    return_url = get_safe_return_url(request)
    if is_htmx_request(request):
        return HttpResponse(headers={"HX-Redirect": return_url})
    return JsonResponse(
        {
            "success": True,
            "username": user.username,
            "redirect_url": return_url,
        }
    )


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

    identifier = request.POST.get(
        "identifier",
        request.POST.get("username", ""),
    ).strip()
    password = request.POST.get("password", "")

    user = authenticate(
        request,
        username=find_login_username(identifier),
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

    login(
        request,
        user,
        backend="django.contrib.auth.backends.ModelBackend",
    )
    return authentication_success(request, user)


@require_POST
def logout_view(request):
    """Завершает пользовательскую сессию."""
    if request.user.is_authenticated:
        PushSubscription.objects.filter(
            user=request.user,
            endpoint__in=request.session.get("push_endpoints", []),
        ).delete()
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
            return registration_form_error(request, form)

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

    login(
        request,
        user,
        backend="django.contrib.auth.backends.ModelBackend",
    )
    return authentication_success(request, user)


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
            "connected_provider_ids": set(
                SocialAccount.objects.filter(user=request.user).values_list(
                    "provider",
                    flat=True,
                )
            ),
            "web_push_enabled": settings.WEB_PUSH_ENABLED,
            "vapid_public_key": settings.VAPID_PUBLIC_KEY,
        },
    )


@login_required
@require_POST
def push_subscribe(request):
    """Создаёт или обновляет подписку текущего браузера."""
    if not settings.WEB_PUSH_ENABLED:
        return JsonResponse({"error": "Web Push не настроен."}, status=503)
    try:
        data = json.loads(request.body)
        endpoint = data["endpoint"]
        keys = data["keys"]
        p256dh = keys["p256dh"]
        auth = keys["auth"]
    except (KeyError, TypeError, ValueError, json.JSONDecodeError):
        return JsonResponse({"error": "Некорректная подписка."}, status=400)
    if (
        not all(isinstance(value, str) and value for value in (endpoint, p256dh, auth))
        or urlparse(endpoint).scheme != "https"
        or len(endpoint) > 1000
        or len(p256dh) > 255
        or len(auth) > 255
    ):
        return JsonResponse({"error": "Некорректная подписка."}, status=400)

    subscription, _ = PushSubscription.objects.update_or_create(
        endpoint=endpoint,
        defaults={
            "user": request.user,
            "p256dh": p256dh,
            "auth": auth,
            "enabled": bool(data.get("enabled", True)),
            "direct_messages_enabled": bool(data.get("directMessages", True)),
        },
    )
    endpoints = set(request.session.get("push_endpoints", []))
    endpoints.add(subscription.endpoint)
    request.session["push_endpoints"] = list(endpoints)
    return JsonResponse({"ok": True, "id": subscription.pk})


@login_required
@require_POST
def push_unsubscribe(request):
    """Удаляет подписку текущего браузера после добровольного отключения."""
    try:
        endpoint = json.loads(request.body)["endpoint"]
    except (KeyError, TypeError, ValueError, json.JSONDecodeError):
        return JsonResponse({"error": "Некорректная подписка."}, status=400)
    PushSubscription.objects.filter(user=request.user, endpoint=endpoint).delete()
    request.session["push_endpoints"] = [
        item
        for item in request.session.get("push_endpoints", [])
        if item != endpoint
    ]
    return JsonResponse({"ok": True})


@login_required
@require_GET
def push_status(request):
    """Возвращает настройки известной подписки текущего браузера."""
    subscription = PushSubscription.objects.filter(
        user=request.user,
        endpoint=request.GET.get("endpoint", ""),
    ).first()
    return JsonResponse(
        {
            "known": subscription is not None,
            "enabled": subscription.enabled if subscription else False,
            "directMessages": (
                subscription.direct_messages_enabled if subscription else True
            ),
        }
    )


@login_required
@require_POST
def disconnect_social_account(request, provider):
    """Отключает провайдера, сохраняя хотя бы один рабочий способ входа."""
    if provider not in {"github", "google"}:
        messages.error(request, "Неизвестный способ входа.")
        return redirect("profile")

    account = SocialAccount.objects.filter(
        user=request.user,
        provider=provider,
    ).first()
    if account is None:
        messages.error(request, "Этот способ входа уже отключён.")
        return redirect("profile")

    connected_count = SocialAccount.objects.filter(user=request.user).count()
    if not request.user.has_usable_password() and connected_count <= 1:
        messages.error(
            request,
            "Сначала задайте пароль или подключите другой сервис.",
        )
        return redirect("profile")

    account.delete()
    messages.success(request, "Способ входа отключён.")
    return redirect("profile")
