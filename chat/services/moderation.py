"""Единые транзакционные решения для админки и мобильной модерации."""
from datetime import timedelta

from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.utils import timezone

from chat.models import Message, MessageReport, ModerationEvent
from users.models import User


class DecisionConflict(Exception):
    """Другой модератор уже изменил состояние жалобы."""


def require_moderator(user, *, write=False):
    if not user.is_active or not user.has_perm('chat.view_messagereport'):
        raise PermissionDenied
    if write and not user.has_perm('chat.moderate_message'):
        raise PermissionDenied


def _reason(reason):
    reason = reason.strip()
    if not reason or len(reason) > 240:
        raise ValidationError('Укажите причину решения: от 1 до 240 символов.')
    return reason


@transaction.atomic
def set_message_visibility(*, actor, message_id, hidden, reason):
    if not actor.has_perm('chat.moderate_message'):
        raise PermissionDenied
    reason = _reason(reason)
    message = Message.objects.select_for_update().get(pk=message_id)
    if bool(message.hidden_at) == hidden:
        return False
    message.hidden_at = timezone.now() if hidden else None
    message.hidden_by = actor if hidden else None
    message.hidden_reason = reason if hidden else ''
    message.save(update_fields=['hidden_at', 'hidden_by', 'hidden_reason'])
    ModerationEvent.objects.create(
        action=ModerationEvent.Action.HIDE_MESSAGE if hidden else ModerationEvent.Action.RESTORE_MESSAGE,
        moderator=actor, target_user=message.user, message=message, reason=reason,
    )
    if hidden:
        from chat.views import broadcast_message_deleted
        transaction.on_commit(lambda: broadcast_message_deleted(message))
    # Restoration appears in history on the next load, never as a new notification.
    return True


@transaction.atomic
def set_user_ban(*, actor, user_id, reason, expires_at=None, remove=False, message=None):
    if not actor.has_perm('users.change_user') or not (
        actor.is_superuser or actor.groups.filter(name='Moderators').exists()
    ):
        raise PermissionDenied
    reason = _reason(reason)
    user = User.objects.select_for_update().get(pk=user_id)
    if user.is_staff or user.is_superuser or user.pk == actor.pk or user.groups.filter(name='Moderators').exists():
        raise PermissionDenied('Нельзя блокировать собственный или служебный аккаунт.')
    user.banned_at = None if remove else timezone.now()
    user.banned_until = None if remove else expires_at
    user.ban_reason = '' if remove else reason
    user.save(update_fields=['banned_at', 'banned_until', 'ban_reason'])
    ModerationEvent.objects.create(
        action=ModerationEvent.Action.UNBAN if remove else ModerationEvent.Action.BAN,
        moderator=actor, target_user=user, message=message, reason=reason, expires_at=None if remove else expires_at,
    )


@transaction.atomic
def decide_report(*, actor, report_id, action, reason, ban_days=None, hide_with_ban=False):
    require_moderator(actor, write=True)
    reason = _reason(reason)
    if action not in ('dismiss', 'hide', 'ban', 'restore'):
        raise ValidationError('Неизвестное решение.')
    message_id = MessageReport.objects.values_list('message_id', flat=True).get(pk=report_id)
    # All decisions and new reports serialize on the same message row.
    message = Message.objects.select_for_update().get(pk=message_id)
    report = MessageReport.objects.select_for_update().get(pk=report_id)
    if action == 'restore':
        if not report.resolved_at or not message.hidden_at:
            raise DecisionConflict('Сообщение уже восстановлено или жалоба ещё не рассмотрена.')
        set_message_visibility(actor=actor, message_id=message.pk, hidden=False, reason=reason)
        return
    if report.resolved_at:
        raise DecisionConflict('Жалоба уже рассмотрена. Обновите страницу.')
    if action == 'hide':
        set_message_visibility(actor=actor, message_id=message.pk, hidden=True, reason=reason)
    if action == 'ban':
        if ban_days not in (1, 7, 0):
            raise ValidationError('Выберите срок блокировки.')
        expires_at = timezone.now() + timedelta(days=ban_days) if ban_days else None
        set_user_ban(actor=actor, user_id=message.user_id, reason=reason, expires_at=expires_at, message=message)
        if hide_with_ban:
            set_message_visibility(actor=actor, message_id=message.pk, hidden=True, reason=reason)
    MessageReport.objects.filter(message=message, resolved_at__isnull=True).update(
        resolved_at=timezone.now(), resolved_by=actor, outcome=action, resolution_note=reason,
    )
    ModerationEvent.objects.create(
        action=ModerationEvent.Action.RESOLVE_REPORT, moderator=actor,
        target_user=message.user, message=message, reason=reason,
    )
