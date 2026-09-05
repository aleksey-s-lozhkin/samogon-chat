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
    welcome_pending = models.BooleanField(default=False)

    @property
    def is_banned(self) -> bool:
        """Возвращает статус временной или бессрочной блокировки."""
        if self.banned_at is None:
            return False
        return self.banned_until is None or self.banned_until > timezone.now()


class PushSubscription(models.Model):
    """Добровольная Web Push-подписка одного браузера пользователя."""

    user = models.ForeignKey(
        User,
        on_delete=models.CASCADE,
        related_name="push_subscriptions",
    )
    endpoint = models.URLField(max_length=1000, unique=True)
    p256dh = models.CharField(max_length=255)
    auth = models.CharField(max_length=255)
    enabled = models.BooleanField(default=True)
    direct_messages_enabled = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ("-updated_at",)

    def __str__(self):
        return f"Web Push: {self.user.username} ({self.pk})"
