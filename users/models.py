from django.contrib.auth.models import AbstractUser
from django.db import models
from django.utils import timezone


class User(AbstractUser):
    """Модель пользователя."""

    avatar = models.ImageField(
        upload_to="avatars/%Y/%m",
        blank=True,
        null=True,
    )
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
    banned_at = models.DateTimeField(blank=True, null=True)
    banned_until = models.DateTimeField(blank=True, null=True)
    ban_reason = models.CharField(blank=True, max_length=240)

    @property
    def is_banned(self) -> bool:
        """Возвращает статус временной или бессрочной блокировки."""
        if self.banned_at is None:
            return False
        return self.banned_until is None or self.banned_until > timezone.now()
