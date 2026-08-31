import uuid
from pathlib import Path

from config import settings
from django.core.exceptions import ValidationError
from django.db import models


class Room(models.Model):
    class Visibility(models.TextChoices):
        PUBLIC = "public", "Открытая"
        PRIVATE = "private", "Тайная"

    name = models.CharField(max_length=100)
    slug = models.SlugField(max_length=100, unique=True)
    description = models.TextField(blank=True)
    visibility = models.CharField(
        max_length=10,
        choices=Visibility.choices,
        default=Visibility.PUBLIC,
    )
    owner = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="owned_chat_rooms",
        blank=True,
        null=True,
    )
    members = models.ManyToManyField(
        settings.AUTH_USER_MODEL,
        through="RoomMembership",
        related_name="private_chat_rooms",
        blank=True,
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["name"]

    def __str__(self):
        return self.name

    @property
    def is_private(self) -> bool:
        return self.visibility == self.Visibility.PRIVATE

    def clean(self):
        if self.is_private and not self.owner_id:
            raise ValidationError("У тайной комнаты должен быть владелец.")
        if not self.is_private and self.owner_id:
            raise ValidationError("У открытой комнаты не может быть владельца.")


class RoomMembership(models.Model):
    """Хранит состав тайной комнаты, включая её владельца."""

    room = models.ForeignKey(
        Room,
        on_delete=models.CASCADE,
        related_name="memberships",
    )
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="private_room_memberships",
    )
    joined_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=("room", "user"),
                name="unique_private_room_member",
            ),
        ]

    def clean(self):
        if not self.room.is_private:
            raise ValidationError("Участники доступны только тайным комнатам.")


class RoomReadState(models.Model):
    """Отметка, до которой пользователь прочитал сообщения в комнате."""

    room = models.ForeignKey(
        Room,
        on_delete=models.CASCADE,
        related_name="read_states",
    )
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="chat_read_states",
    )
    last_read_at = models.DateTimeField()

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=("room", "user"),
                name="unique_room_read_state",
            ),
        ]


class Message(models.Model):
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="chat_messages",
    )
    room = models.ForeignKey(
        Room,
        on_delete=models.CASCADE,
        related_name="messages",
    )
    recipient = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="received_chat_messages",
        blank=True,
        null=True,
    )
    text = models.TextField()
    hidden_at = models.DateTimeField(blank=True, null=True)
    hidden_reason = models.CharField(blank=True, max_length=240)
    hidden_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        related_name="hidden_chat_messages",
        blank=True,
        null=True,
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["created_at"]
        permissions = [
            ("moderate_message", "Can moderate chat messages"),
        ]

    def __str__(self):
        return f"{self.user.username}: {self.text}"


def attachment_upload_to(instance, filename):
    """Скрывает исходное имя файла за случайным именем в закрытом каталоге."""
    suffix = Path(filename).suffix.lower()
    return f"chat/attachments/{instance.id.hex}{suffix}"


class Attachment(models.Model):
    """Метаданные безопасного вложения, привязанного к одному сообщению."""

    class Kind(models.TextChoices):
        IMAGE = "image", "Изображение"
        FILE = "file", "Файл"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    message = models.ForeignKey(
        Message,
        on_delete=models.CASCADE,
        related_name="attachments",
    )
    file = models.FileField(upload_to=attachment_upload_to)
    original_name = models.CharField(max_length=255)
    content_type = models.CharField(max_length=100)
    size = models.PositiveIntegerField()
    kind = models.CharField(max_length=10, choices=Kind.choices)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["created_at"]

    def __str__(self):
        return self.original_name


class ModerationEvent(models.Model):
    """Хранит решения модераторов, не смешивая их с содержимым чата."""

    class Action(models.TextChoices):
        BAN = "ban", "Блокировка"
        UNBAN = "unban", "Разблокировка"
        HIDE_MESSAGE = "hide_message", "Скрытие сообщения"
        RESTORE_MESSAGE = "restore_message", "Восстановление сообщения"

    action = models.CharField(max_length=20, choices=Action.choices)
    moderator = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        related_name="moderation_actions",
        blank=True,
        null=True,
    )
    target_user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="moderation_events",
    )
    message = models.ForeignKey(
        Message,
        on_delete=models.SET_NULL,
        related_name="moderation_events",
        blank=True,
        null=True,
    )
    reason = models.CharField(blank=True, max_length=240)
    expires_at = models.DateTimeField(blank=True, null=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return f"{self.get_action_display()}: {self.target_user.username}"
