from datetime import timedelta

from django import forms
from django.contrib.auth.forms import UserChangeForm
from django.contrib import admin
from django.contrib import messages
from django.contrib.auth.admin import UserAdmin as BaseUserAdmin
from django.core.exceptions import PermissionDenied
from django.db.models import OuterRef, Subquery
from django.shortcuts import redirect, render
from django.urls import path, reverse
from django.utils import timezone

from users.forms import AdminPushForm
from users.models import ChatStatus, PushSubscription, User
from users.services.push import send_admin_push


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
    change_list_template = "admin/users/pushsubscription/change_list.html"

    def get_urls(self):
        custom_urls = [
            path(
                "send/",
                self.admin_site.admin_view(self.send_push_view),
                name="users_pushsubscription_send",
            ),
        ]
        return custom_urls + super().get_urls()

    def send_push_view(self, request):
        """Показывает суперпользователю тест и подтверждаемую общую рассылку."""
        if not request.user.is_superuser:
            raise PermissionDenied

        form = AdminPushForm(request.POST or None)
        if request.method == "POST" and form.is_valid():
            subscriptions = PushSubscription.objects.filter(enabled=True)
            if form.cleaned_data["audience"] == AdminPushForm.AUDIENCE_SELF:
                subscriptions = subscriptions.filter(user=request.user)
            result = send_admin_push(
                subscriptions=subscriptions,
                title=form.cleaned_data["title"],
                body=form.cleaned_data["body"],
                url=form.cleaned_data["url"],
            )
            messages.success(
                request,
                "Push отправлен: принято push-службой — "
                f"{result.delivered}, ошибок — {result.failed}, "
                f"удалено недействительных — {result.removed}.",
            )
            return redirect(reverse("admin:users_pushsubscription_changelist"))

        context = {
            **self.admin_site.each_context(request),
            "opts": self.model._meta,
            "title": "Отправить Web Push",
            "form": form,
        }
        return render(request, "admin/users/pushsubscription/send_push.html", context)

    def has_add_permission(self, request):
        return False


@admin.register(ChatStatus)
class ChatStatusAdmin(admin.ModelAdmin):
    list_display = ("label", "code", "position", "is_active")
    list_editable = ("position", "is_active")
    search_fields = ("label", "code")
    list_filter = ("is_active",)

    def get_readonly_fields(self, request, obj=None):
        return ("code",) if obj else ()

    def has_delete_permission(self, request, obj=None):
        # Codes remain valid for users who already selected a retired status.
        return False


class ChatUserChangeForm(UserChangeForm):
    presence_status = forms.ChoiceField(label="Статус в чате", required=False)

    class Meta(UserChangeForm.Meta):
        model = User

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        if "presence_status" in self.fields:
            self.fields["presence_status"].choices = ChatStatus.choices_for(
                self.instance.presence_status,
            )


@admin.register(User)
class UserAdmin(BaseUserAdmin):
    """Класс вывода пользователей в админке."""

    model = User
    form = ChatUserChangeForm

    list_display = (
        'id',
        'username',
        'email',
        'is_staff',
        'is_active',
        'ban_status',
        'presence_status_label',
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
        ('Профиль в чате', {'fields': ('presence_status',)}),
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

    def get_queryset(self, request):
        return super().get_queryset(request).annotate(
            chat_status_label=Subquery(
                ChatStatus.objects.filter(code=OuterRef("presence_status")).values("label")[:1],
            ),
        )

    @admin.display(description="Статус в чате")
    def presence_status_label(self, user):
        return user.chat_status_label or "Без статуса"

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

    def get_fieldsets(self, request, obj=None):
        fieldsets = super().get_fieldsets(request, obj)
        if request.user.is_superuser:
            return fieldsets
        return tuple(
            (name, {**options, "fields": tuple(
                "presence_status_label" if field == "presence_status" else field
                for field in options["fields"]
            )})
            for name, options in fieldsets
        )

    def get_readonly_fields(self, request, obj=None):
        """Модератор работает только действиями, а не редактирует профиль."""
        if request.user.is_superuser:
            return ()
        return tuple(field.name for field in self.model._meta.fields) + (
            "groups",
            "user_permissions",
            "presence_status_label",
        )

    def has_add_permission(self, request):
        return request.user.is_superuser

    def has_delete_permission(self, request, obj=None):
        return request.user.is_superuser

    def apply_ban(self, request, queryset, expires_at):
        """Блокирует обычные аккаунты и сохраняет решение в журнале."""
        from chat.services.moderation import set_user_ban
        users = queryset.filter(is_staff=False, is_superuser=False).exclude(pk=request.user.pk).exclude(groups__name="Moderators")
        for user_id in users.values_list("pk", flat=True):
            set_user_ban(actor=request.user, user_id=user_id, reason="Решение модератора.", expires_at=expires_at)

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
        from chat.services.moderation import set_user_ban
        users = queryset.filter(is_staff=False, is_superuser=False).exclude(groups__name="Moderators")
        for user_id in users.values_list("pk", flat=True):
            set_user_ban(actor=request.user, user_id=user_id, reason="Блокировка снята модератором.", remove=True)
