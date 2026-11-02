"""Единственное место, где собирается запрос к общей модели.

Лапоть и Самогон работают на одной загруженной `qwen3:8b` (ADR-0010 в
репозитории лаптя). Договор держится на значениях, которые легко разъехаться:
имя модели, `num_ctx`, `keep_alive`, таймаут и `think`. Пока тело запроса
собиралось бы в нескольких местах, второе место рано или поздно разошлось бы
с первым — и поломка была бы тихой: всё работает, просто в разы медленнее и с
перезагрузками модели.

Поэтому здесь только сборка тела и его отправка. Характер персонажа остаётся
в своём файле промпта и уходит обычным системным сообщением: это параметр
запроса, а не модели.
"""

from __future__ import annotations

import json
import re
from http.client import HTTPException
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from django.conf import settings


class OllamaUnavailable(RuntimeError):
    """Модель недоступна, ответила ошибкой или пустотой."""


#: Reasoning-модель может прислать цепочку размышлений. В чат она не идёт.
THINKING_TRACE = re.compile(r"<think>.*?</think>\s*", re.DOTALL)


def chat_payload(
    messages: list[dict[str, str]],
    *,
    temperature: float,
    num_predict: int,
) -> dict:
    """Собирает тело запроса из настроек, а не из литералов.

    Значения модели и контекста берутся только отсюда: зашитое в код число
    сделало бы расхождение с лаптем невидимым.
    """
    return {
        "model": settings.OLLAMA_MODEL,
        "stream": False,
        "think": False,
        "keep_alive": settings.OLLAMA_KEEP_ALIVE,
        "options": {
            "temperature": temperature,
            "num_ctx": settings.OLLAMA_NUM_CTX,
            "num_predict": num_predict,
        },
        "messages": messages,
    }


def send_chat(payload: dict) -> str:
    """Отправляет готовое тело и возвращает ответ без следов размышлений."""
    try:
        # Сборка запроса внутри try: битый OLLAMA_BASE_URL даёт ValueError
        # прямо здесь, ещё до сети.
        request = Request(
            f"{settings.OLLAMA_BASE_URL.rstrip('/')}/api/chat",
            data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urlopen(request, timeout=settings.OLLAMA_TIMEOUT_SECONDS) as response:
            data = json.load(response)
    except (
        # HTTPError и URLError — обычные сетевые отказы, HTTPException — рваный
        # ответ (BadStatusLine, IncompleteRead), ValueError — битый адрес или
        # JSON. Всё это означает одно: ответа нет, надо молчать, а не падать.
        HTTPError,
        URLError,
        HTTPException,
        TimeoutError,
        OSError,
        json.JSONDecodeError,
        ValueError,
    ) as error:
        raise OllamaUnavailable from error

    if not isinstance(data, dict):
        raise OllamaUnavailable
    message = data.get("message")
    if not isinstance(message, dict):
        raise OllamaUnavailable
    content = message.get("content")
    if not isinstance(content, str) or not content.strip():
        raise OllamaUnavailable
    return THINKING_TRACE.sub("", content).strip()
