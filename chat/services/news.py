"""Новость дня из Hacker News.

Зачем отдельный модуль: птице нужны свежие поводы, а курируемый список
конечен. Но внешний заголовок — это **данные из интернета**, а не инструкция,
и модель может домыслить то, чего в заголовке нет. Поэтому здесь три правила:

1. Заголовок очищается и обрезается: только текст, без разметки.
2. Модель имеет **право промолчать**: если заголовок непонятен, она отвечает
   словом-маркером, и повод пропускается, а не выдумывается.
3. Числа в готовой реплике должны быть в заголовке. Придуманная цифра —
   признак выдумки, и такая реплика отбрасывается.
"""

from __future__ import annotations

import json
import re
import urllib.request
from dataclasses import dataclass

from django.conf import settings

from chat.services.ollama import (
    OllamaUnavailable,
    chat_payload,
    send_chat,
    unload_model,
)

#: Что модель отвечает, если не поняла заголовок. Латиница и кириллица.
SKIP_MARKERS = ("ПРОПУСК", "ПРОПУСК.", "SKIP")

#: Сколько новостей в сутки максимум.
NEWS_TOPIC_PREFIX = "news-"

HTML_TAG = re.compile(r"<[^>]+>")
NUMBER = re.compile(r"\d+")


@dataclass(frozen=True)
class Story:
    id: str
    title: str
    points: int


def _clean_title(raw: str) -> str:
    """Только текст: без тегов, лишних пробелов и обрезанный по длине."""
    text = HTML_TAG.sub(" ", raw or "")
    text = " ".join(text.split())
    return text[: settings.BIRD_NEWS_MAX_TITLE]


def fetch_stories(*, limit: int = 30, min_points: int | None = None) -> list[Story]:
    """Свежие истории с Hacker News, отфильтрованные по рейтингу.

    Сеть может не ответить — это не ошибка птицы, поэтому вызывающий код
    просто вернётся к курируемому списку.
    """
    request = urllib.request.Request(settings.BIRD_NEWS_URL, method="GET")
    with urllib.request.urlopen(request, timeout=settings.BIRD_NEWS_TIMEOUT) as response:
        payload = json.load(response)

    stories = []
    for hit in payload.get("hits", [])[:limit]:
        title = _clean_title(hit.get("title") or "")
        points = int(hit.get("points") or 0)
        object_id = str(hit.get("objectID") or "")
        if not title or not object_id:
            continue
        threshold = (
            settings.BIRD_NEWS_MIN_POINTS if min_points is None else min_points
        )
        if points < threshold:
            continue
        stories.append(Story(id=object_id, title=title, points=points))
    return stories


def compose_news_line(story: Story, *, ask=None) -> str | None:
    """Пересказ новости по-русски. None — модель промолчала или выдумала.

    Модель для новостей — отдельная (обычно 14B): заголовки на английском и
    требуют перевода, а 8B на незнакомых названиях домысливает.
    """
    prompt = (
        f"Заголовок новости: {story.title}\n"
        "Перескажи эту новость по-русски одной короткой репликой. Это новость: "
        "подай её словами «пишут», «сообщают».\n"
        "Не добавляй чисел, имён и деталей, которых в заголовке нет.\n"
        "Если заголовок непонятен или в нём нет техники — ответь ровно словом "
        "ПРОПУСК и больше ничего."
    )
    model = settings.BIRD_NEWS_MODEL or settings.OLLAMA_CHARACTER_MODEL or None
    if model and model != settings.OLLAMA_MODEL:
        # Освобождаем видеопамять: общая модель закреплена и не даст загрузиться.
        unload_model(settings.OLLAMA_MODEL)
    request = (ask or send_chat)(
        chat_payload(
            [
                {"role": "system", "content": _persona()},
                {"role": "user", "content": prompt},
            ],
            temperature=0.5,
            num_predict=120,
            model=model,
            keep_alive=0,
        )
    )
    line = request.strip().strip('"').strip()
    if not line:
        return None
    if line.upper().startswith(tuple(marker for marker in SKIP_MARKERS)):
        return None
    if not _numbers_are_grounded(line, story.title):
        return None
    return line


def _persona() -> str:
    from chat.services.bird import load_persona

    return load_persona()


def _numbers_are_grounded(line: str, title: str) -> bool:
    """Все числа реплики должны быть в заголовке.

    Проверка грубая намеренно: она ловит именно придуманные цифры, а не
    стилистику. Если модель сказала «в 2027 году», а в заголовке 2028 —
    публиковать нельзя.
    """
    allowed = set(NUMBER.findall(title))
    return all(number in allowed for number in NUMBER.findall(line))


def news_topic_id(story: Story) -> str:
    """Идентификатор повода: по нему работает история «не повторять»."""
    return f"{NEWS_TOPIC_PREFIX}{story.id}"


def pick_news_topic(
    *,
    recent_ids: set[str],
    attempts: int = 3,
    min_points: int | None = None,
) -> dict | None:
    """Первый подходящий свежий повод или None.

    Если сеть недоступна или модель промолчала про все попытки — None, и
    вызывающий код берёт курируемый повод. Птица не обязана говорить новость.
    """
    try:
        stories = fetch_stories(min_points=min_points)
    except (OSError, ValueError, KeyError, json.JSONDecodeError) as error:
        import logging

        logging.getLogger(__name__).warning(
            "bird_news_unavailable %s", type(error).__name__
        )
        return None

    fresh = [story for story in stories if news_topic_id(story) not in recent_ids]
    for story in fresh[:attempts]:
        try:
            line = compose_news_line(story)
        except OllamaUnavailable:
            return None
        if line:
            return {
                "id": news_topic_id(story),
                "title": story.title[:80],
                "tag": "новость",
                "kind": "новость",
                "fact": story.title,
                "line": line,
            }
    return None
