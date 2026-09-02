from django.contrib import admin
from django.utils import timezone

from .models import (
    Attachment,
    Message,
    MessageReaction,
    ModerationEvent,
    Room,
    RoomMembership,
    RoomReadState,
)


@admin.register(Room)
class RoomAdmin(admin.ModelAdmin):
    list_display = ("name", "slug", "visibility", "owner", "created_at")
    list_filter = ("visibility",)
    prepopulated_fields = {"slug": ("name",)}


@admin.register(Message)
class MessageAdmin(admin.ModelAdmin):
    list_display = ("user", "room", "text", "hidden_at", "created_at")
    list_filter = ("room",)
    actions = ("hide_messages", "restore_messages")

    @admin.action(description="Скрыть выбранные сообщения")
    def hide_messages(self, request, queryset):
        """Убирает сообщения из чата, сохраняя их для проверки в админке."""
        messages = queryset.filter(hidden_at__isnull=True)
        now = timezone.now()
        messages.update(
            hidden_at=now,
            hidden_by=request.user,
            hidden_reason="Скрыто модератором.",
        )
        ModerationEvent.objects.bulk_create(
            [
                ModerationEvent(
                    action=ModerationEvent.Action.HIDE_MESSAGE,
                    moderator=request.user,
                    target_user=message.user,
                    message=message,
                    reason="Скрыто модератором.",
                )
                for message in messages.select_related("user")
            ]
        )

    @admin.action(description="Вернуть выбранные сообщения")
    def restore_messages(self, request, queryset):
        """Возвращает в ленту ранее скрытые сообщения."""
        messages = queryset.filter(hidden_at__isnull=False)
        messages.update(hidden_at=None, hidden_by=None, hidden_reason="")
        ModerationEvent.objects.bulk_create(
            [
                ModerationEvent(
                    action=ModerationEvent.Action.RESTORE_MESSAGE,
                    moderator=request.user,
                    target_user=message.user,
                    message=message,
                    reason="Сообщение возвращено модератором.",
                )
                for message in messages.select_related("user")
            ]
        )

    def get_readonly_fields(self, request, obj=None):
        """Модератор скрывает сообщения действием, но не редактирует текст."""
        if request.user.is_superuser:
            return ()
        return (
            "user",
            "room",
            "recipient",
            "text",
            "created_at",
            "hidden_at",
            "hidden_by",
            "hidden_reason",
        )


@admin.register(RoomMembership)
class RoomMembershipAdmin(admin.ModelAdmin):
    list_display = ("room", "user", "joined_at")


@admin.register(RoomReadState)
class RoomReadStateAdmin(admin.ModelAdmin):
    list_display = ("room", "user", "last_read_at")


@admin.register(Attachment)
class AttachmentAdmin(admin.ModelAdmin):
    """Файлы можно проверить, но нельзя подменить содержимое через админку."""

    list_display = ("original_name", "message", "kind", "size", "created_at")
    list_filter = ("kind",)
    search_fields = ("original_name", "message__user__username")
    readonly_fields = (
        "message",
        "file",
        "original_name",
        "content_type",
        "size",
        "kind",
        "created_at",
    )

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False


@admin.register(MessageReaction)
class MessageReactionAdmin(admin.ModelAdmin):
    """Реакции видны для разбора жалоб, но не редактируются вручную."""

    list_display = ("emoji", "user", "message", "created_at")
    list_filter = ("emoji",)
    search_fields = ("user__username", "message__text")
    readonly_fields = ("emoji", "user", "message", "created_at")

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False


@admin.register(ModerationEvent)
class ModerationEventAdmin(admin.ModelAdmin):
    """Журнал решений доступен для проверки, но не редактируется вручную."""

    list_display = (
        "action",
        "target_user",
        "moderator",
        "expires_at",
        "created_at",
    )
    list_filter = ("action",)
    search_fields = ("target_user__username", "reason")
    readonly_fields = (
        "action",
        "moderator",
        "target_user",
        "message",
        "reason",
        "expires_at",
        "created_at",
    )

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False
