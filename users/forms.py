from io import BytesIO
from secrets import compare_digest

from django import forms
from django.conf import settings
from django.contrib.auth import get_user_model
from django.contrib.auth.password_validation import validate_password
from django.core.exceptions import ValidationError
from django.core.files.base import ContentFile

from .utils import resize_avatar

User = get_user_model()


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

    invite_code = forms.CharField(
        label="Код приглашения",
        required=False,
        widget=forms.PasswordInput(attrs={"autocomplete": "off"}),
    )

    class Meta:
        model = User
        fields = (
            "username",
            "email",
            "password",
        )

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

    class Meta:
        model = User
        fields = (
            "username",
            "email",
            "avatar",
            "message_color",
        )

        labels = {
            "username": "Имя пользователя",
            "email": "Email",
            "avatar": "Аватар",
            "message_color": "Цвет моих сообщений",
        }

        widgets = {
            "username": forms.TextInput(
                attrs={
                    "placeholder": "Введите имя пользователя",
                }
            ),
            "email": forms.EmailInput(
                attrs={
                    "placeholder": "Введите email",
                }
            ),
        }

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
