import uuid
from pathlib import Path

from config import settings
from django.core.exceptions import ValidationError
from django.db import models


class Room(models.Model):
    class Visibility(models.TextChoices):
        PUBLIC = "public", "Открытая"
        PRIVATE = "private", "Закрытая"

    name = models.CharField(max_length=100, verbose_name="Название")
    slug = models.SlugField(max_length=100, unique=True, verbose_name="Адрес беседы")
    description = models.TextField(blank=True, verbose_name="Описание")
    visibility = models.CharField(
        max_length=10,
        choices=Visibility.choices,
        default=Visibility.PUBLIC,
        verbose_name="Доступность",
    )
    owner = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="owned_chat_rooms",
        blank=True,
        null=True,
        verbose_name="Владелец",
    )
    members = models.ManyToManyField(
        settings.AUTH_USER_MODEL,
        through="RoomMembership",
        verbose_name="Участники",
        related_name="private_chat_rooms",
        blank=True,
    )
    created_at = models.DateTimeField(auto_now_add=True, verbose_name="Создано")

    class Meta:
        verbose_name = "Беседа"
        verbose_name_plural = "Беседы"
        ordering = ["name"]
        constraints = [
            models.UniqueConstraint(
                fields=("owner",),
                condition=models.Q(visibility="private"),
                name="one_owned_private_room_per_user",
            ),
        ]

    def __str__(self):
        return self.name

    @property
    def is_private(self) -> bool:
        return self.visibility == self.Visibility.PRIVATE

    def clean(self):
        if self.is_private and not self.owner_id:
            raise ValidationError("У закрытой беседы должен быть владелец.")
        if not self.is_private and self.owner_id:
            raise ValidationError("У открытой комнаты не может быть владельца.")


class RoomMembership(models.Model):
    """Хранит состав закрытой беседы, включая её владельца."""

    room = models.ForeignKey(
        Room,
        on_delete=models.CASCADE,
        related_name="memberships",
        verbose_name="Беседа",
    )
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="private_room_memberships",
        verbose_name="Пользователь",
    )
    joined_at = models.DateTimeField(auto_now_add=True, verbose_name="Присоединился")

    class Meta:
        verbose_name = "Участник беседы"
        verbose_name_plural = "Участники бесед"
        constraints = [
            models.UniqueConstraint(
                fields=("room", "user"),
                name="unique_private_room_member",
            ),
        ]

    def clean(self):
        if not self.room.is_private:
            raise ValidationError("Участники доступны только закрытым беседам.")


class RoomReadState(models.Model):
    """Отметка, до которой пользователь прочитал сообщения в комнате."""

    room = models.ForeignKey(
        Room,
        on_delete=models.CASCADE,
        related_name="read_states",
        verbose_name="Беседа",
    )
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="chat_read_states",
        verbose_name="Пользователь",
    )
    last_read_at = models.DateTimeField( verbose_name="Прочитано до")

    class Meta:
        verbose_name = "Статус прочтения"
        verbose_name_plural = "Статусы прочтения"
        constraints = [
            models.UniqueConstraint(
                fields=("room", "user"),
                name="unique_room_read_state",
            ),
        ]


class Message(models.Model):
    reply_to = models.ForeignKey(
        "self", on_delete=models.SET_NULL, related_name="replies", blank=True, null=True,
        verbose_name="Ответ на сообщение",
    )
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="chat_messages",
        verbose_name="Пользователь",
    )
    room = models.ForeignKey(
        Room,
        on_delete=models.CASCADE,
        related_name="messages",
        verbose_name="Беседа",
    )
    recipient = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="received_chat_messages",
        blank=True,
        null=True,
        verbose_name="Получатель",
    )
    text = models.TextField( verbose_name="Текст")
    hidden_at = models.DateTimeField(blank=True, null=True, verbose_name="Скрыто")
    hidden_reason = models.CharField(blank=True, max_length=240, verbose_name="Причина скрытия")
    hidden_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        related_name="hidden_chat_messages",
        blank=True,
        null=True,
        verbose_name="Скрыл",
    )
    created_at = models.DateTimeField(auto_now_add=True, verbose_name="Создано")

    class Meta:
        verbose_name = "Сообщение"
        verbose_name_plural = "Сообщения"
        ordering = ["created_at"]
        permissions = [
            ("moderate_message", "Can moderate chat messages"),
        ]

    def __str__(self):
        return f"{self.user.username}: {self.text}"


class MessageReaction(models.Model):
    """Одна реакция одного гостя на конкретную реплику."""

    class Emoji(models.TextChoices):
        LIKE = "👍", "Нравится"
        DISLIKE = "👎", "Не нравится"
        HEART = "❤️", "Любовь"
        LAUGH = "😂", "Смешно"
        FIRE = "🔥", "Огонь"
        SURPRISED = "😮", "Удивлён"
        SAD = "😢", "Грустно"
        THINKING = "🤔", "Задумался"
        HANDSHAKE = "🤝", "Согласен"
        CELEBRATE = "🎉", "Праздную"

    message = models.ForeignKey(
        Message,
        on_delete=models.CASCADE,
        related_name="reactions",
        verbose_name="Сообщение",
    )
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="chat_reactions",
        verbose_name="Пользователь",
    )
    emoji = models.CharField(max_length=8, choices=Emoji.choices, verbose_name="Эмодзи")
    created_at = models.DateTimeField(auto_now_add=True, verbose_name="Создано")

    class Meta:
        verbose_name = "Реакция"
        verbose_name_plural = "Реакции"
        constraints = [
            models.UniqueConstraint(
                fields=("message", "user", "emoji"),
                name="unique_message_reaction",
            ),
        ]
        indexes = [models.Index(fields=("message", "emoji"))]

    def __str__(self):
        return f"{self.user.username}: {self.emoji}"


class Note(models.Model):
    """Личная заметка гостя, при необходимости сохранённая из реплики."""

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="chat_notes",
        verbose_name="Пользователь",
    )
    text = models.TextField(max_length=1000, verbose_name="Текст")
    source_message = models.ForeignKey(
        Message,
        on_delete=models.SET_NULL,
        related_name="saved_notes",
        blank=True,
        null=True,
        verbose_name="Исходное сообщение",
    )
    source_author = models.CharField("Автор исходного сообщения", blank=True, max_length=150)
    created_at = models.DateTimeField(auto_now_add=True, verbose_name="Создано")

    class Meta:
        verbose_name = "Заметка"
        verbose_name_plural = "Заметки"
        ordering = ["-created_at"]
        constraints = [
            models.UniqueConstraint(
                fields=("user", "source_message"),
                condition=models.Q(source_message__isnull=False),
                name="unique_saved_message_note",
            ),
        ]

    def __str__(self):
        return f"Заметка {self.user.username}: {self.text[:40]}"


def note_attachment_upload_to(instance, filename):
    """Отделяет копии вложений в заметках от исходной реплики."""
    suffix = Path(filename).suffix.lower()
    return f"chat/note-attachments/{instance.id.hex}{suffix}"


class NoteAttachment(models.Model):
    """Личная копия вложения из сохранённой реплики."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    note = models.ForeignKey(
        Note,
        on_delete=models.CASCADE,
        related_name="attachments",
        verbose_name="Заметка",
    )
    file = models.FileField(upload_to=note_attachment_upload_to, verbose_name="Файл")
    original_name = models.CharField(max_length=255, verbose_name="Исходное имя файла")
    content_type = models.CharField(max_length=100, verbose_name="Тип содержимого")
    size = models.PositiveIntegerField( verbose_name="Размер в байтах")
    kind = models.CharField(
        max_length=10,
        choices=(("image", "Изображение"), ("file", "Файл"), ("audio", "Аудио")),
        verbose_name="Вид",
    )
    created_at = models.DateTimeField(auto_now_add=True, verbose_name="Создано")

    class Meta:
        verbose_name = "Вложение заметки"
        verbose_name_plural = "Вложения заметок"
        ordering = ["created_at"]

    def __str__(self):
        return self.original_name


def attachment_upload_to(instance, filename):
    """Скрывает исходное имя файла за случайным именем в закрытом каталоге."""
    suffix = Path(filename).suffix.lower()
    return f"chat/attachments/{instance.id.hex}{suffix}"


class Attachment(models.Model):
    """Метаданные безопасного вложения, привязанного к одному сообщению."""

    class Kind(models.TextChoices):
        IMAGE = "image", "Изображение"
        FILE = "file", "Файл"
        AUDIO = "audio", "Аудио"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    message = models.ForeignKey(
        Message,
        on_delete=models.CASCADE,
        related_name="attachments",
        verbose_name="Сообщение",
    )
    file = models.FileField(upload_to=attachment_upload_to, verbose_name="Файл")
    original_name = models.CharField(max_length=255, verbose_name="Исходное имя файла")
    content_type = models.CharField(max_length=100, verbose_name="Тип содержимого")
    size = models.PositiveIntegerField( verbose_name="Размер в байтах")
    kind = models.CharField(max_length=10, choices=Kind.choices, verbose_name="Вид")
    duration_ms = models.PositiveIntegerField("Длительность в мс", blank=True, null=True)
    created_at = models.DateTimeField(auto_now_add=True, verbose_name="Создано")

    class Meta:
        verbose_name = "Вложение"
        verbose_name_plural = "Вложения"
        ordering = ["created_at"]

    def __str__(self):
        return self.original_name


class ModerationEvent(models.Model):
    """Хранит решения модераторов, не смешивая их с содержимым чата."""

    class Action(models.TextChoices):
        RESOLVE_REPORT = "resolve_report", "Рассмотрение жалоб"
        BAN = "ban", "Блокировка"
        UNBAN = "unban", "Разблокировка"
        HIDE_MESSAGE = "hide_message", "Скрытие сообщения"
        RESTORE_MESSAGE = "restore_message", "Восстановление сообщения"

    action = models.CharField(max_length=20, choices=Action.choices, verbose_name="Действие")
    moderator = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        related_name="moderation_actions",
        blank=True,
        null=True,
        verbose_name="Модератор",
    )
    target_user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="moderation_events",
        verbose_name="Пользователь",
    )
    message = models.ForeignKey(
        Message,
        on_delete=models.SET_NULL,
        related_name="moderation_events",
        blank=True,
        null=True,
        verbose_name="Сообщение",
    )
    reason = models.CharField(blank=True, max_length=240, verbose_name="Причина")
    expires_at = models.DateTimeField(blank=True, null=True, verbose_name="Действует до")
    created_at = models.DateTimeField(auto_now_add=True, verbose_name="Создано")

    class Meta:
        verbose_name = "Событие модерации"
        verbose_name_plural = "Журнал модерации"
        ordering = ["-created_at"]

    def __str__(self):
        return f"{self.get_action_display()}: {self.target_user.username}"


class MessageReport(models.Model):
    """Жалоба пользователя для ручного решения модератором."""

    class Outcome(models.TextChoices):
        DISMISS = "dismiss", "Жалоба отклонена"
        HIDE = "hide", "Сообщение скрыто"
        BAN = "ban", "Автор заблокирован"

    outcome = models.CharField("Решение", max_length=16, choices=Outcome.choices, blank=True, db_default="")
    resolution_note = models.CharField("Комментарий к решению", max_length=240, blank=True, db_default="")

    class Reason(models.TextChoices):
        ABUSE = "abuse", "Оскорбление или травля"
        SPAM = "spam", "Спам или реклама"
        PRIVACY = "privacy", "Личные данные"
        OTHER = "other", "Другое"

    message = models.ForeignKey(
        Message,
        on_delete=models.CASCADE,
        related_name="reports",
        verbose_name="Сообщение",
    )
    reporter = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="message_reports",
        verbose_name="Заявитель",
    )
    reason = models.CharField(max_length=16, choices=Reason.choices, verbose_name="Причина")
    details = models.CharField(blank=True, max_length=240, verbose_name="Пояснение")
    resolved_at = models.DateTimeField(blank=True, null=True, verbose_name="Рассмотрено")
    resolved_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        related_name="resolved_message_reports",
        blank=True,
        null=True,
        verbose_name="Рассмотрел",
    )
    created_at = models.DateTimeField(auto_now_add=True, verbose_name="Создано")

    class Meta:
        verbose_name = "Жалоба"
        verbose_name_plural = "Жалобы"
        ordering = ("-created_at",)
        constraints = [
            models.UniqueConstraint(
                fields=("message", "reporter"),
                name="unique_message_reporter",
            ),
        ]

    def __str__(self):
        return f"Жалоба на сообщение {self.message_id}"


class BartenderJob(models.Model):
    """Сохраняемое состояние фонового обращения к Семёну."""

    class Status(models.TextChoices):
        QUEUED = "queued", "В очереди"
        STARTED = "started", "Выполняется"
        SUCCEEDED = "succeeded", "Готово"
        FAILED = "failed", "Ошибка"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="bartender_jobs",
        verbose_name="Пользователь",
    )
    room = models.ForeignKey(Room, on_delete=models.CASCADE, related_name="bartender_jobs", verbose_name="Беседа")
    question = models.ForeignKey(
        Message,
        on_delete=models.CASCADE,
        related_name="bartender_question_jobs",
        verbose_name="Вопрос",
    )
    response = models.ForeignKey(
        Message,
        on_delete=models.SET_NULL,
        related_name="bartender_response_jobs",
        blank=True,
        null=True,
        verbose_name="Ответ",
    )
    private = models.BooleanField(default=False, verbose_name="Личное обращение")
    status = models.CharField(
        max_length=12,
        choices=Status.choices,
        default=Status.QUEUED,
        db_index=True,
        verbose_name="Статус",
    )
    error_code = models.CharField(max_length=32, blank=True, verbose_name="Код ошибки")
    created_at = models.DateTimeField(auto_now_add=True, verbose_name="Создано")
    started_at = models.DateTimeField(blank=True, null=True, verbose_name="Начато")
    finished_at = models.DateTimeField(blank=True, null=True, verbose_name="Завершено")

    class Meta:
        verbose_name = "Задание Семёна"
        verbose_name_plural = "Задания Семёна"
        ordering = ("-created_at",)


class AtmosphereLine(models.Model):
    """Короткая строка для шапки, публикуемая только после модерации."""

    class Kind(models.TextChoices):
        SEMEN = "semen", "Реплика Семёна"
        PEP = "pep", "Цитата PEP"

    class Status(models.TextChoices):
        DRAFT = "draft", "На модерации"
        APPROVED = "approved", "Одобрена"
        REJECTED = "rejected", "Отклонена"

    text = models.CharField(max_length=140, unique=True, verbose_name="Текст")
    kind = models.CharField(max_length=12, choices=Kind.choices, verbose_name="Вид")
    status = models.CharField(
        max_length=12,
        choices=Status.choices,
        default=Status.DRAFT,
        db_index=True,
        verbose_name="Статус",
    )
    source_label = models.CharField(max_length=80, blank=True, verbose_name="Источник")
    source_url = models.URLField(blank=True, verbose_name="Ссылка на источник")
    generated_by_model = models.CharField(max_length=120, blank=True, verbose_name="Модель генерации")
    is_active = models.BooleanField(default=True, verbose_name="Активно")
    approved_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        related_name="approved_atmosphere_lines",
        blank=True,
        null=True,
        verbose_name="Одобрил",
    )
    approved_at = models.DateTimeField(blank=True, null=True, verbose_name="Одобрено")
    created_at = models.DateTimeField(auto_now_add=True, verbose_name="Создано")

    class Meta:
        verbose_name = "Атмосферная строка"
        verbose_name_plural = "Атмосферные строки"
        ordering = ("kind", "text")

    def __str__(self):
        return self.text
