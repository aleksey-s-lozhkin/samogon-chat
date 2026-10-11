"""Решает, можно ли беспокоить человека уведомлением.

Один вопрос — «слать или не слать» — и три причины ответить «нет»:
человек выбрал не получать такое, у него тихие часы, или он и так сидит
в приложении. Собранные в одном месте, эти причины объяснимы: каждое
«нет» возвращается с поводом, и в логах видно, почему уведомление не
ушло, — а без этого «почему мне не пришло» превращается в гадание.

Почему тихие часы и часовой пояс живут здесь, а не в подписке: **спит
человек, а не телефон**. Иначе ноутбук будил бы среди ночи, пока телефон
молчит, а на новом устройстве тишину пришлось бы настраивать заново.
"""

from __future__ import annotations

import logging
from datetime import datetime
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from django.conf import settings
from django.utils import timezone as django_timezone

from chat.services.presence import is_user_online_sync
from users.models import NotificationSettings, PushSubscription

logger = logging.getLogger(__name__)

#: Виды уведомлений. Строками, а не числами: значение попадает в лог
#: причины, и «direct» читается, а «1» — нет.
KIND_DIRECT = "direct"
KIND_ROOM = "room"

#: Какие виды кому соответствуют. Режим подписки — ответ на вопрос «что
#: присылать на это устройство», и таблица говорит, что в него входит.
MODE_KINDS = {
    PushSubscription.Mode.DIRECT: {KIND_DIRECT},
    PushSubscription.Mode.ALL: {KIND_DIRECT, KIND_ROOM},
}


def in_quiet_hours(hour: int, *, quiet_from: int, quiet_to: int) -> bool:
    """Попадает ли час в тихие часы.

    Окно может пересекать полночь, и ``quiet_from > quiet_to`` — это
    нормальный случай, а не ошибка ввода: 22:00–09:00 именно так и
    выглядит. Окно нулевой длины означает «тихих часов нет».

    Логика повторяет лапот намеренно: одинаковые правила в двух проектах
    одного человека лучше, чем два похожих, но разных.
    """
    if quiet_from == quiet_to:
        return False
    if quiet_from < quiet_to:
        return quiet_from <= hour < quiet_to
    return hour >= quiet_from or hour < quiet_to


def settings_for(user) -> NotificationSettings:
    """Настройки уведомлений, создавая их при первом обращении.

    Создаются лениво, а не сигналом при регистрации: заводить строку
    каждому, кто зарегистрировался и ушёл, — мусор в базе ради удобства
    кода.
    """
    settings_row, _ = NotificationSettings.objects.get_or_create(user=user)
    return settings_row


def local_now(user) -> datetime:
    """Текущее время в поясе человека.

    Пояс может быть не задан или назван с ошибкой — тогда берём пояс
    проекта. Молча падать из-за опечатки в названии пояса нельзя: тихие
    часы перестали бы работать целиком.
    """
    configured = settings_for(user).timezone
    if configured:
        try:
            return django_timezone.now().astimezone(ZoneInfo(configured))
        except (ZoneInfoNotFoundError, ValueError):
            logger.warning(
                "Неизвестный часовой пояс %s у пользователя %s, беру пояс проекта",
                configured,
                user.pk,
            )
    return django_timezone.localtime()


def should_notify(user, *, kind: str) -> tuple[bool, str]:
    """Слать ли уведомление этого вида.

    :return: решение и причина. Причина нужна и при «да»: в логах должно
        быть видно не только почему не отправили, но и на каком основании
        отправили.
    """
    if kind not in (KIND_DIRECT, KIND_ROOM):
        raise ValueError(f"неизвестный вид уведомления: {kind}")

    now_local = local_now(user)
    quiet = settings_for(user)
    if in_quiet_hours(now_local.hour, quiet_from=quiet.quiet_from, quiet_to=quiet.quiet_to):
        return False, f"тихие часы ({now_local.hour}:00 по местному)"

    # Проверка присутствия — последняя: она единственная ходит в Redis, и
    # при тихих часах незачем его дёргать.
    if is_user_online_sync(user.username):
        return False, "человек сейчас в приложении"

    return True, "можно"


def allows(subscription: PushSubscription, *, kind: str) -> bool:
    """Входит ли этот вид уведомлений в выбранный на устройстве режим."""
    return kind in MODE_KINDS.get(subscription.mode, set())


def subscriptions_for(*, user, kind: str):
    """Подписки, которым положено это уведомление.

    Фильтр по режиму идёт в базе, а не в Python: подписок у человека
    может быть несколько, а лишние строки незачем доставать из базы,
    чтобы тут же отбросить.
    """
    modes = [mode for mode, kinds in MODE_KINDS.items() if kind in kinds]
    return PushSubscription.objects.filter(user=user, mode__in=modes)
