"""Нормализация пользовательского статуса для профиля и WebSocket."""

from django.core.exceptions import ValidationError

CUSTOM_STATUS_MAX_LENGTH = 40

# Текущая редакция публичных правил. Меняется вместе с templates/service-rules.html,
# чтобы по записи в аккаунте было видно, что именно принял гость.
RULES_VERSION = "2026-09-29"

# Имя технического аккаунта бармена нельзя занимать: иначе обычный гость
# подхватывается как Семён и говорит от его лица.
RESERVED_USERNAMES = {"semen", "семён", "семен"}


def normalize_custom_status(value: str) -> str:
    """Приводит свой статус к безопасному однострочному виду.

    Значение показывается другим гостям, поэтому ограничиваем длину, убираем
    управляющие символы и запрещаем ссылки.
    """
    text = " ".join(str(value or "").split())
    if len(text) > CUSTOM_STATUS_MAX_LENGTH:
        raise ValidationError(
            f"Свой статус — не больше {CUSTOM_STATUS_MAX_LENGTH} символов."
        )
    if any(char in text for char in "<>{}"):
        raise ValidationError("Свой статус не может содержать < > { }.")
    lowered = text.lower()
    if "http://" in lowered or "https://" in lowered or "www." in lowered:
        raise ValidationError("Ссылки в своём статусе не поддерживаются.")
    return text
