"""Кто в системе персонаж, а не гость.

Семён и Птица-Говорун — обычные учётные записи, но не люди. Разница видна в
четырёх местах сразу: их нельзя пригласить в закрытую беседу, им нельзя
написать лично, их не показывают в списке гостей и их не считают гостями,
когда решается, можно ли птице говорить.

Пока персонаж был один, проверки писались по месту и сравнивали имя с
`BARTENDER_USERNAME`. Со вторым персонажем такая проверка размножилась бы:
десять мест, в каждом легко забыть про новое имя. Поэтому одним списком
управляет этот модуль, а всё остальное спрашивает `is_bot`.
"""

from __future__ import annotations

from django.conf import settings


def bot_usernames() -> set[str]:
    """Имена всех персонажей. Пустые значения настроек пропускаются."""
    names = {settings.BARTENDER_USERNAME, settings.BIRD_USERNAME}
    return {name for name in names if name}


def is_bot(user) -> bool:
    """Персонаж ли это. Для анонимного и отсутствующего пользователя — нет."""
    if user is None:
        return False
    if getattr(user, "is_anonymous", False):
        return False
    return getattr(user, "username", "") in bot_usernames()
