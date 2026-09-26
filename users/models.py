from django.contrib.auth.models import AbstractUser
from django.db import models
from django.utils import timezone


class ChatStatus(models.Model):
    code = models.SlugField("Код", max_length=24, unique=True)
    label = models.CharField("Название", max_length=80)
    position = models.PositiveIntegerField("Порядок", default=0)
    is_active = models.BooleanField("Доступен для выбора", default=True)

    class Meta:
        ordering = ("position", "id")
        verbose_name = "статус в чате"
        verbose_name_plural = "Статусы в чате"

    def __str__(self):
        return self.label

    @classmethod
    def choices_for(cls, current=""):
        statuses = cls.objects.filter(models.Q(is_active=True) | models.Q(code=current))
        return [("", "Без статуса"), *statuses.values_list("code", "label")]


class User(AbstractUser):
    """Модель пользователя."""

    # Stable legacy codes retained for integrations; editable labels live in ChatStatus.
    class PresenceStatus(models.TextChoices):
        READING = "reading", "Читаю, но не отвечаю"
        EATING = "eating", "Кушаю"
        BEER = "beer", "Пью пиво"
        THINKING = "thinking", "Думаю"
        SMOKING = "smoking", "Ушёл курить"
        BACK_SOON = "back_soon", "Скоро вернусь"

    avatar = models.ImageField(
        upload_to="avatars/%Y/%m",
        blank=True,
        null=True,
        verbose_name="Аватар",
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
        verbose_name="Цвет сообщений",
    )
    presence_status = models.CharField(
        "Статус в чате",
        max_length=24,
        blank=True,
    )
    last_seen_at = models.DateTimeField(
        "Последняя активность в чате",
        blank=True,
        null=True,
    )
    banned_at = models.DateTimeField(blank=True, null=True, verbose_name="Заблокирован")
    banned_until = models.DateTimeField(blank=True, null=True, verbose_name="Блокировка до")
    ban_reason = models.CharField(blank=True, max_length=240, verbose_name="Причина блокировки")
    welcome_pending = models.BooleanField(default=False, verbose_name="Ожидает приветствия")

    def get_presence_status_display(self):
        if not self.presence_status:
            return ""
        return ChatStatus.objects.filter(code=self.presence_status).values_list(
            "label", flat=True,
        ).first() or self.presence_status

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
        verbose_name="Пользователь",
    )
    endpoint = models.URLField(max_length=1000, unique=True, verbose_name="Адрес push-службы")
    p256dh = models.CharField(max_length=255, verbose_name="Открытый ключ подписки")
    auth = models.CharField(max_length=255, verbose_name="Секрет подписки")
    enabled = models.BooleanField(default=True, verbose_name="Включена")
    direct_messages_enabled = models.BooleanField(default=True, verbose_name="Уведомления о личных сообщениях")
    created_at = models.DateTimeField(auto_now_add=True, verbose_name="Создано")
    updated_at = models.DateTimeField(auto_now=True, verbose_name="Обновлено")

    class Meta:
        verbose_name = "Подписка на уведомления"
        verbose_name_plural = "Подписки на уведомления"
        ordering = ("-updated_at",)

    def __str__(self):
        return f"Web Push: {self.user.username} ({self.pk})"
