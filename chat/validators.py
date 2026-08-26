import json


def validate_message(text_data):
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

    if len(message) > 1000:
        return None, "Сообщение не может быть длиннее 1000 символов"

    return message, None
