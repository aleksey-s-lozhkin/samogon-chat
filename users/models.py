from django.contrib.auth.models import AbstractUser
from django.db import models


class User(AbstractUser):
    """Модель пользователя."""
    avatar = models.ImageField(upload_to='avatars/%Y/%m', blank=True, null=True)
    message_color = models.CharField(
        max_length=16,
        choices=(
            ("amber", "Янтарный"),
            ("blue", "Ночной синий"),
            ("sage", "Шалфейный"),
            ("plum", "Сливовый"),
        ),
        default="amber",
    )
