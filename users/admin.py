from datetime import timedelta

from django.contrib import admin
from django.contrib.auth.admin import UserAdmin as BaseUserAdmin
from django.utils import timezone

from chat.models import ModerationEvent

from users.models import PushSubscription, User


@admin.register(PushSubscription)
class PushSubscriptionAdmin(admin.ModelAdmin):
    """Показывает подписки без endpoint и криптографических ключей."""

    list_display = ("id", "user", "enabled", "direct_messages_enabled", "updated_at")
    list_filter = ("enabled", "direct_messages_enabled")
    search_fields = ("user__username", "user__email")
    fields = (
        "user",
        "enabled",
        "direct_messages_enabled",
        "created_at",
        "updated_at",
    )
    readonly_fields = (
        "user",
        "enabled",
        "direct_messages_enabled",
        "created_at",
        "updated_at",
    )

    def has_add_permission(self, request):
        return False


@admin.register(User)
class UserAdmin(BaseUserAdmin):
    """Класс вывода пользователей в админке."""

    model = User

    list_display = (
        'id',
        'username',
        'email',
        'is_staff',
        'is_active',
        'ban_status',
        'date_joined',
    )
    search_fields = ('email',)
    list_filter = (
        'is_staff',
        'is_active',
        'is_superuser',
        'banned_at',
    )
    ordering = ('email',)
    actions = ('ban_for_day', 'ban_for_week', 'ban_permanently', 'unban_users')
    add_fieldsets = (
        (
            None,
            {
                'classes': ('wide',),
                'fields': (
                    'username',
                    'email',
                    'password1',
                    'password2',
                ),
            },
        ),
    )
    fieldsets = (
        (
            None,
            {
                'fields': (
                    'username',
                    'email',
                    'password',
                )
            },
        ),
        (
            'Права доступа',
            {
                'fields': (
                    'is_active',
                    'is_staff',
                    'is_superuser',
                    'groups',
                    'user_permissions',
                )
            },
        ),
        (
            'Модерация',
            {
                'fields': (
                    'banned_at',
                    'banned_until',
                    'ban_reason',
                )
            },
        ),
    )

    @admin.display(description='Блокировка', boolean=True)
    def ban_status(self, user):
        return user.is_banned

    def is_moderator(self, request):
        """Проверяет группу без привязки к отображаемому имени пользователя."""
        return request.user.is_superuser or request.user.groups.filter(
            name="Moderators"
        ).exists()

    def get_actions(self, request):
        actions = super().get_actions(request)
        if request.user.is_superuser:
            return actions
        if not self.is_moderator(request):
            return {}
        return {
            name: action
            for name, action in actions.items()
            if name in {
                "ban_for_day",
                "ban_for_week",
                "ban_permanently",
                "unban_users",
            }
        }

    def get_readonly_fields(self, request, obj=None):
        """Модератор работает только действиями, а не редактирует профиль."""
        if request.user.is_superuser:
            return ()
        return tuple(field.name for field in self.model._meta.fields) + (
            "groups",
            "user_permissions",
        )

    def has_add_permission(self, request):
        return request.user.is_superuser

    def has_delete_permission(self, request, obj=None):
        return request.user.is_superuser

    def apply_ban(self, request, queryset, expires_at):
        """Блокирует обычные аккаунты и сохраняет решение в журнале."""
        users = queryset.filter(is_staff=False).exclude(pk=request.user.pk)
        now = timezone.now()
        users.update(
            banned_at=now,
            banned_until=expires_at,
            ban_reason='Решение модератора.',
        )
        ModerationEvent.objects.bulk_create(
            [
                ModerationEvent(
                    action=ModerationEvent.Action.BAN,
                    moderator=request.user,
                    target_user=user,
                    reason='Решение модератора.',
                    expires_at=expires_at,
                )
                for user in users
            ]
        )

    @admin.action(description='Заблокировать на сутки')
    def ban_for_day(self, request, queryset):
        self.apply_ban(request, queryset, timezone.now() + timedelta(days=1))

    @admin.action(description='Заблокировать на неделю')
    def ban_for_week(self, request, queryset):
        self.apply_ban(request, queryset, timezone.now() + timedelta(days=7))

    @admin.action(description='Заблокировать навсегда')
    def ban_permanently(self, request, queryset):
        self.apply_ban(request, queryset, None)

    @admin.action(description='Снять блокировку')
    def unban_users(self, request, queryset):
        """Снимает активные и истёкшие блокировки с выбранных пользователей."""
        users = queryset.filter(is_staff=False)
        users.update(banned_at=None, banned_until=None, ban_reason='')
        ModerationEvent.objects.bulk_create(
            [
                ModerationEvent(
                    action=ModerationEvent.Action.UNBAN,
                    moderator=request.user,
                    target_user=user,
                    reason='Блокировка снята модератором.',
                )
                for user in users
            ]
        )
