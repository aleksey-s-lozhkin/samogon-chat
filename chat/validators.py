import json


MESSAGE_MAX_LENGTH = 1000


def validate_message(text_data):
    """Проверяет входящий WebSocket-пакет до обращения к базе данных."""
    try:
        data = json.loads(text_data)
    except json.JSONDecodeError:
        return None, "Некорректный JSON"

    if not isinstance(data, dict):
        return None, "Сообщение должно быть JSON-объектом"

    message = data.get("message")

    if not isinstance(message, str):
        return None, "Поле message должно быть строкой"

    message = message.strip()

    if not message:
        return None, "Сообщение не может быть пустым"

    if len(message) > MESSAGE_MAX_LENGTH:
        return (
            None,
            f"Сообщение не может быть длиннее {MESSAGE_MAX_LENGTH} символов",
        )

    return message, None
