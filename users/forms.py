from io import BytesIO
from secrets import compare_digest

from django import forms
from django.conf import settings
from django.contrib.auth import get_user_model
from django.contrib.auth.password_validation import validate_password
from django.core.exceptions import ValidationError
from django.core.files.base import ContentFile
from django.utils import timezone

from .models import ChatStatus
from .statuses import (
    CUSTOM_STATUS_MAX_LENGTH,
    RESERVED_USERNAMES,
    RULES_VERSION,
    normalize_custom_status,
)
from .utils import resize_avatar

User = get_user_model()
PUBLIC_USERNAME_MAX_LENGTH = 32


def validate_public_username(value: str) -> str:
    """Проверяет публичное имя на занятость служебных вариантов."""
    username = (value or "").strip()
    if username.casefold() in RESERVED_USERNAMES:
        raise forms.ValidationError("Это имя занято барменом. Выберите другое.")
    return username


class RegistrationForm(forms.ModelForm):
    """Форма регистрации пользователя."""

    password = forms.CharField(
        label="Пароль",
        widget=forms.PasswordInput,
    )
    email = forms.EmailField(
        label="Email",
        required=True,
    )
    username = forms.CharField(
        label="Имя в чате",
        max_length=PUBLIC_USERNAME_MAX_LENGTH,
    )

    invite_code = forms.CharField(
        label="Код приглашения",
        required=False,
        widget=forms.PasswordInput(attrs={"autocomplete": "off"}),
    )

    accept_rules = forms.BooleanField(
        label=(
            "Мне 18 лет, я принимаю правила сервиса и уведомлён об обработке "
            "данных"
        ),
        required=True,
        error_messages={
            "required": "Отметьте согласие с правилами, чтобы продолжить.",
        },
    )

    class Meta:
        model = User
        fields = (
            "username",
            "email",
            "password",
        )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        if settings.REGISTRATION_OPEN:
            self.fields.pop("invite_code", None)

    def clean_username(self):
        return validate_public_username(self.cleaned_data["username"])

    def clean_email(self):
        email = self.cleaned_data["email"].strip().lower()
        if User.objects.filter(email__iexact=email).exists():
            raise forms.ValidationError(
                "Аккаунт с таким email уже есть. Попробуйте войти."
            )
        return email

    def clean_password(self):
        password = self.cleaned_data["password"]
        candidate = User(
            username=self.data.get("username", "").strip(),
            email=self.data.get("email", "").strip(),
        )
        try:
            validate_password(password, user=candidate)
        except ValidationError as error:
            raise forms.ValidationError(error.messages) from error
        return password

    def clean(self):
        cleaned_data = super().clean()

        if settings.REGISTRATION_OPEN:
            return cleaned_data

        invite_code = cleaned_data.get("invite_code", "")
        expected_code = settings.REGISTRATION_INVITE_CODE
        if expected_code and not compare_digest(invite_code, expected_code):
            self.add_error("invite_code", "Код приглашения не подошёл.")
        elif not settings.DEBUG and not expected_code:
            self.add_error(
                "invite_code",
                "Регистрация временно доступна только по приглашению.",
            )

        return cleaned_data

    def save(self, commit=True):
        user = super().save(commit=False)

        user.set_password(self.cleaned_data["password"])
        user.welcome_pending = True
        # Фиксируем факт и версию принятых правил: иначе доказать согласие нечем.
        user.accepted_rules_at = timezone.now()
        user.accepted_rules_version = RULES_VERSION

        if commit:
            user.save()

        return user


class AdminPushForm(forms.Form):
    """Проверенная форма административного Web Push."""

    AUDIENCE_SELF = "self"
    AUDIENCE_ALL = "all"

    audience = forms.ChoiceField(
        label="Получатели",
        choices=(
            (AUDIENCE_SELF, "Только мои устройства (проверка)"),
            (AUDIENCE_ALL, "Все активные подписки"),
        ),
    )
    title = forms.CharField(label="Заголовок", max_length=80)
    body = forms.CharField(
        label="Текст уведомления",
        max_length=180,
        widget=forms.Textarea(attrs={"rows": 3}),
        help_text="Этот текст может быть виден на заблокированном экране.",
    )
    url = forms.CharField(
        label="Ссылка внутри Самогона",
        max_length=300,
        initial="/chat/",
        help_text="Например: /chat/ — внешние ссылки запрещены.",
    )
    confirm = forms.BooleanField(
        label="Подтверждаю отправку выбранным получателям",
    )

    def clean_url(self):
        url = self.cleaned_data["url"].strip()
        if not url.startswith("/") or url.startswith("//"):
            raise forms.ValidationError("Укажите внутренний путь, начинающийся с /.")
        return url


class ProfileForm(forms.ModelForm):
    """Форма редактирования профиля пользователя."""

    presence_status = forms.ChoiceField(
        label="Статус в чате",
        choices=(),
        required=False,
    )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["presence_status"].choices = ChatStatus.choices_for(
            self.instance.presence_status,
        )

    class Meta:
        model = User
        fields = (
            "username",
            "email",
            "avatar",
            "message_color",
            "presence_status",
            "custom_status",
        )

        labels = {
            "username": "Имя пользователя",
            "email": "Email",
            "avatar": "Аватар",
            "message_color": "Цвет моих сообщений",
            "presence_status": "Статус в чате",
            "custom_status": "Свой статус",
        }

        widgets = {
            "username": forms.TextInput(
                attrs={
                    "placeholder": "Введите имя пользователя",
                    "maxlength": PUBLIC_USERNAME_MAX_LENGTH,
                }
            ),
            "email": forms.EmailInput(
                attrs={
                    "placeholder": "Введите email",
                }
            ),
            "custom_status": forms.TextInput(
                attrs={
                    "placeholder": "Например: чиню прод",
                    "maxlength": CUSTOM_STATUS_MAX_LENGTH,
                }
            ),
        }

    def clean_username(self):
        username = self.cleaned_data["username"].strip()
        if (
            len(username) > PUBLIC_USERNAME_MAX_LENGTH
            and username != self.instance.username
        ):
            raise forms.ValidationError(
                f"Используйте не больше {PUBLIC_USERNAME_MAX_LENGTH} символов."
            )
        return validate_public_username(username)

    def clean_email(self):
        """Не даёт занять email другого аккаунта.

        Иначе вход по email перестаёт находить обоих пользователей.
        """
        email = self.cleaned_data["email"].strip().lower()
        duplicate = User.objects.filter(email__iexact=email).exclude(
            pk=self.instance.pk,
        )
        if email and duplicate.exists():
            raise forms.ValidationError(
                "Этот email уже используется другим аккаунтом."
            )
        return email

    def clean_custom_status(self):
        return normalize_custom_status(self.cleaned_data.get("custom_status", ""))

    def clean_avatar(self):
        avatar = self.cleaned_data.get("avatar")

        if not avatar:
            return avatar

        try:
            resized_image = resize_avatar(avatar)
        except Exception as error:
            # Ошибка изображения не должна ронять страницу профиля.
            raise forms.ValidationError(
                "Не удалось обработать изображение."
            ) from error

        buffer = BytesIO()

        resized_image.save(
            buffer,
            format="JPEG",
            quality=90,
        )

        return ContentFile(
            buffer.getvalue(),
            name="avatar.jpg",
        )
