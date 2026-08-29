from io import BytesIO

from django import forms
from django.contrib.auth import get_user_model
from django.core.files.base import ContentFile

from .utils import resize_avatar

User = get_user_model()


class RegistrationForm(forms.ModelForm):
    """Форма регистрации пользователя."""

    password = forms.CharField(
        label="Пароль",
        widget=forms.PasswordInput,
    )

    password_confirm = forms.CharField(
        label="Повторите пароль",
        widget=forms.PasswordInput,
    )

    class Meta:
        model = User
        fields = (
            "username",
            "email",
            "password",
            "password_confirm",
        )

    def clean(self):
        cleaned_data = super().clean()

        password = cleaned_data.get("password")
        password_confirm = cleaned_data.get("password_confirm")

        if password and password_confirm and password != password_confirm:
            raise forms.ValidationError("Пароли не совпадают.")

        return cleaned_data

    def save(self, commit=True):
        user = super().save(commit=False)

        user.set_password(self.cleaned_data["password"])

        if commit:
            user.save()

        return user

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
