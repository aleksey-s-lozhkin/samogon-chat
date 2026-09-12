import json
import re
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from django.conf import settings


ATMOSPHERE_SYSTEM_PROMPT = """
Ты пишешь одну короткую атмосферную строку для камерного чата программистов.
Тон спокойный, доброжелательный, с редким сухим юмором. Можно упомянуть Python, код, тесты, логи,
деплой, рефакторинг или отдых после задачи. Не пиши об алкоголе, еде, других людях или текущих событиях.
Не выдавай текст за цитату и не ссылайся на PEP. Не используй Markdown, эмодзи, кавычки, приветствия или вопросы.
Длина — от 4 до 12 слов и не более 100 символов. Выведи только саму строку.
""".strip()
CYRILLIC = re.compile(r"[А-Яа-яЁё]")
FORBIDDEN = re.compile(
    r"(?:https?://|www\.|@|[`*_#]|\b(?:пив\w*|вино|вина|вину|вином|вине|водк\w*|виски|коктейл\w*|бармен\w*)\b)",
    re.IGNORECASE,
)


class AtmosphereUnavailable(Exception):
    """Генератор не вернул безопасную строку."""


def validate_atmosphere_line(value):
    value = " ".join(value.strip().split())
    if not 4 <= len(value.split()) <= 12:
        raise AtmosphereUnavailable
    if len(value) > 100 or not CYRILLIC.search(value) or FORBIDDEN.search(value):
        raise AtmosphereUnavailable
    return value


def generate_atmosphere_line():
    payload = {
        "model": settings.OLLAMA_MODEL,
        "stream": False,
        "think": False,
        "keep_alive": settings.OLLAMA_KEEP_ALIVE,
        "options": {
            "temperature": 0.9,
            "num_ctx": 2048,
            "num_predict": 60,
        },
        "messages": [
            {"role": "system", "content": ATMOSPHERE_SYSTEM_PROMPT},
            {"role": "user", "content": "Напиши новую строку."},
        ],
    }
    request = Request(
        f"{settings.OLLAMA_BASE_URL.rstrip('/')}/api/chat",
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urlopen(request, timeout=settings.OLLAMA_TIMEOUT_SECONDS) as response:
            data = json.load(response)
    except (HTTPError, URLError, TimeoutError, OSError, json.JSONDecodeError) as error:
        raise AtmosphereUnavailable from error
    content = data.get("message", {}).get("content")
    if not isinstance(content, str):
        raise AtmosphereUnavailable
    content = re.sub(r"<think>.*?</think>\s*", "", content, flags=re.DOTALL)
    return validate_atmosphere_line(content)
