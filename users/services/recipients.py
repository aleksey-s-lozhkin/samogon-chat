"""Кому можно писать лично — одно правило на все входы.

Личное сообщение приходит тремя путями: WebSocket, веб-форма с вложением и
HTTP API. Правила у них обязаны совпадать, иначе один путь оказывается дырой:
пока проверка жила в трёх местах, через форму с вложением можно было написать
боту, хотя списки получателей его уже не показывали.

Правило: получатель существует, активен, не забанен, не суперпользователь, не
молчаливый персонаж (птица), не сам отправитель, а в закрытой беседе — ещё и её
участник. Бармен остаётся доступен: личный вопрос ему — существующая функция.
"""

from __future__ import annotations

from users.models import User
from users.services.bots import unavailable_bot_usernames


def resolve_recipient(*, username: str, viewer, room=None):
    """Возвращает получателя личного сообщения или None, если писать нельзя."""
    name = (username or "").strip()
    if not name or viewer is None:
        return None

    # Сначала точное совпадение: регистрация не запрещает Alice и alice, и
    # поиск без регистра мог бы отправить личное сообщение не тому человеку.
    user = User.objects.filter(username=name).first()
    if user is None:
        # Регистр не совпал — принимаем только однозначный вариант.
        candidates = list(User.objects.filter(username__iexact=name)[:2])
        user = candidates[0] if len(candidates) == 1 else None
    if user is None or user.pk == viewer.pk:
        return None
    if not user.is_active or user.is_superuser or user.is_banned:
        return None
    if user.username in unavailable_bot_usernames():
        return None
    if room is not None and room.is_private:
        if not room.memberships.filter(user=user).exists():
            return None
    return user
