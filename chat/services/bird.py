"""Птица-Говорун: второй персонаж, который говорит в пустой комнате.

Задача сервиса — ответить на два вопроса и не смешивать их:

1. **Стоит ли говорить?** Ответ возвращается с причиной, как в
   `users/services/notifications.py`: «рано», «тихий час», «в комнате гости».
   Без причины «почему промолчала» превращается в гадание, а птица, говорящая
   не вовремя, раздражает быстрее, чем молчащая.
2. **Что сказать?** Повод берётся из списка, реплика — у общей модели.
   Модель и контекст не свои: договор с лаптем (ADR-0010) держится на
   `chat/services/ollama.py`.

Сервис ничего не знает про расписание: его вызывает management-команда (а
позже — задача Celery). Поэтому он синхронный и без циклов событий.
"""

from __future__ import annotations

import json
import logging
import random
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from django.conf import settings
from django.contrib.auth import get_user_model
from django.utils import timezone

from chat.models import BirdPost, Message, Room
from chat.services.events import broadcast_message
from chat.services.jobs import enqueue_bartender_job
from chat.services.language import needs_language_retry
from chat.services.ollama import OllamaUnavailable, chat_payload, send_chat
from chat.services.presence import room_users_sync
from chat.services.messages import MessageService
from users.services.bots import bot_usernames
from users.services.notifications import in_quiet_hours

logger = logging.getLogger(__name__)

TOPICS_PATH = Path(__file__).with_name("prompts") / "bird-topics.json"
PERSONA_PATH = Path(__file__).with_name("prompts") / "bird.txt"

#: Выше, чем у Семёна: птица должна звучать по-разному каждый раз.
BIRD_TEMPERATURE = 0.85
BIRD_NUM_PREDICT = 90

#: Повторная попытка, если модель смешала языки.
LANGUAGE_RETRY = (
    "Перепиши ответ ниже только грамотным русским языком, без иероглифов и "
    "английского текста. Сохрани смысл, оставь одну короткую фразу.\n\nОтвет: "
)


@dataclass(frozen=True)
class Decision:
    """Говорить ли и почему. Причина нужна и в логах, и в выводе команды."""

    speak: bool
    reason: str


def load_topics() -> list[dict]:
    """Читает список поводов. Битый файл — не повод падать."""
    try:
        payload = json.loads(TOPICS_PATH.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        logger.warning("bird_topics_unavailable path=%s", TOPICS_PATH)
        return []
    topics = payload.get("topics")
    return topics if isinstance(topics, list) else []


def load_persona() -> str:
    try:
        return PERSONA_PATH.read_text(encoding="utf-8").strip()
    except OSError:
        logger.warning("bird_persona_unavailable path=%s", PERSONA_PATH)
        return "Ты — Птица-Говорун в баре. Говори по-русски коротко."


def quiet_window() -> tuple[int, int]:
    """Разбирает «1-8» в пару часов. Некорректное значение — без тишины."""
    raw = settings.BIRD_QUIET_HOURS.strip()
    if "-" not in raw:
        return (0, 0)
    start, _, end = raw.partition("-")
    try:
        return (int(start), int(end))
    except ValueError:
        logger.warning("bird_quiet_hours_invalid value=%s", raw)
        return (0, 0)


def local_now(now: datetime | None = None) -> datetime:
    """Время в эталонном поясе птицы.

    Комната общая: «ночь для каждого гостя» не определить, поэтому тихие часы
    считаются по одному поясу и это записано в документации.
    """
    moment = now or timezone.now()
    try:
        zone = ZoneInfo(settings.BIRD_QUIET_TIMEZONE)
    except (ZoneInfoNotFoundError, ValueError):
        logger.warning(
            "bird_timezone_unknown value=%s",
            settings.BIRD_QUIET_TIMEZONE,
        )
        zone = ZoneInfo("UTC")
    return moment.astimezone(zone)


def guests_in_room(room_slug: str, *, room_users=None) -> list[str]:
    """Гости комнаты: персонажи и суперпользователи гостями не считаются.

    Суперпользователь — это хозяин заведения: его присутствие не должно
    выключать птицу, иначе он никогда не увидит разговор живьём. Правило
    совпадает с `eligible_guests()`, где суперпользователи тоже не гости.
    """
    if room_users is None:
        room_users = room_users_sync(room_slug)

    names = [name for name in room_users if name not in bot_usernames()]
    if not names:
        return []

    owner_names = get_user_model().objects.filter(
        username__in=names,
        is_superuser=True,
    ).values_list("username", flat=True)
    owners = set(owner_names)
    return [name for name in names if name not in owners]


def get_bird_user():
    """Технический пользователь птицы.

    Неактивный и без пароля: под ним нельзя войти, но его реплики — обычные
    сообщения, видимые в комнате.
    """
    user_model = get_user_model()
    user, created = user_model.objects.get_or_create(
        username=settings.BIRD_USERNAME,
        defaults={"is_active": False},
    )
    if created:
        user.set_unusable_password()
        user.save(update_fields=["password"])
    return user


def pick_topic(*, room: Room, rng=None) -> dict | None:
    """Выбирает повод, не повторяя последние.

    Без истории список из 70 тем начал бы ходить по кругу и повторяться
    заметно быстро.
    """
    topics = load_topics()
    if not topics:
        return None

    recent = set(
        BirdPost.objects.filter(room=room)
        .order_by("-created_at")
        .values_list("topic_id", flat=True)[: settings.BIRD_TOPIC_HISTORY]
    )
    fresh = [topic for topic in topics if topic.get("id") not in recent]
    pool = fresh or topics
    return (rng or random).choice(pool)


def decide(*, now=None, room_users=None) -> Decision:
    """Стоит ли говорить прямо сейчас и почему именно так."""
    if not settings.BIRD_ENABLED:
        return Decision(False, "выключено настройкой BIRD_ENABLED")

    slug = settings.BIRD_ROOM_SLUG.strip()
    if not slug:
        return Decision(False, "не задана комната BIRD_ROOM_SLUG")

    room = Room.objects.filter(slug=slug).first()
    if room is None:
        return Decision(False, f"комната {slug!r} не найдена")

    guests = guests_in_room(slug, room_users=room_users)
    if guests:
        return Decision(False, f"в комнате гости: {len(guests)}")

    moment = local_now(now)
    quiet_from, quiet_to = quiet_window()
    if in_quiet_hours(moment.hour, quiet_from=quiet_from, quiet_to=quiet_to):
        return Decision(False, f"тихий час ({moment.hour}:00)")

    last = (
        BirdPost.objects.filter(room=room)
        .order_by("-created_at")
        .values_list("created_at", flat=True)
        .first()
    )
    if last is not None:
        elapsed = (now or timezone.now()) - last
        if elapsed < timedelta(minutes=settings.BIRD_INTERVAL_MINUTES):
            return Decision(
                False,
                f"рано: прошло {int(elapsed.total_seconds() // 60)} мин "
                f"из {settings.BIRD_INTERVAL_MINUTES}",
            )

    day_start = moment.replace(hour=0, minute=0, second=0, microsecond=0)
    said_today = BirdPost.objects.filter(
        room=room,
        created_at__gte=day_start,
    ).count()
    if said_today >= settings.BIRD_DAILY_LIMIT:
        return Decision(False, f"дневной потолок: {said_today}")

    return Decision(True, "в комнате никого, пора говорить")


def compose_line(topic: dict, *, ask=None) -> str:
    """Просит у модели одну короткую реплику по поводу."""
    kind = topic.get("kind")
    if kind == "байка":
        hint = "Это байка: подай её как «говорят» или «рассказывают»."
    elif kind == "шутка":
        hint = "Это скорее шутка, чем факт: не выдавай её за открытие."
    else:
        hint = "Это факт: не добавляй деталей, которых в нём нет."

    prompt = (
        f"Повод: {topic.get('fact', '').strip()}\n"
        f"{hint}\n"
        "Скажи одну короткую реплику по этому поводу. Только реплику, без пояснений."
    )
    request = (ask or send_chat)(
        chat_payload(
            [
                {"role": "system", "content": load_persona()},
                {"role": "user", "content": prompt},
            ],
            temperature=BIRD_TEMPERATURE,
            num_predict=BIRD_NUM_PREDICT,
        )
    )
    return request.strip().strip('"').strip()


def _acceptable(text: str) -> bool:
    return bool(text) and len(text) <= settings.BIRD_MAX_LENGTH


def speak(*, force=False, dry_run=False, now=None, room_users=None, ask=None, rng=None):
    """Говорит, если пора. Возвращает текст реплики или None.

    ``force`` обходит паузы (интервал, потолок, тишину, гостей) — для обкатки
    руками. Выключенная настройка и незаданная комната не обходятся никогда:
    иначе однажды птица заговорит там, где её не ждали.
    """
    if force:
        if not settings.BIRD_ENABLED:
            return _refuse("выключено настройкой BIRD_ENABLED")
        slug = settings.BIRD_ROOM_SLUG.strip()
        if not slug:
            return _refuse("не задана комната BIRD_ROOM_SLUG")
        room = Room.objects.filter(slug=slug).first()
        if room is None:
            return _refuse(f"комната {slug!r} не найдена")
    else:
        decision = decide(now=now, room_users=room_users)
        if not decision.speak:
            return _refuse(decision.reason)
        room = Room.objects.get(slug=settings.BIRD_ROOM_SLUG.strip())

    topic = pick_topic(room=room, rng=rng)
    if topic is None:
        return _refuse("нет поводов: файл bird-topics.json пуст или недоступен")

    try:
        text = compose_line(topic, ask=ask)
    except OllamaUnavailable:
        return _refuse("модель недоступна")

    if needs_language_retry(text):
        try:
            text = compose_line(topic, ask=ask)
        except OllamaUnavailable:
            return _refuse("модель недоступна")
        if needs_language_retry(text):
            return _refuse("модель ответила не по-русски")

    if not _acceptable(text):
        return _refuse("реплика пустая или слишком длинная")

    logger.info(
        "bird_speaks topic=%s length=%d room=%s force=%s",
        topic.get("id"),
        len(text),
        room.slug,
        force,
    )
    if dry_run:
        return text

    return _publish(room=room, topic=topic, text=text)


def _refuse(reason: str):
    logger.info("bird_silent reason=%s", reason)
    return None


def _publish(*, room: Room, topic: dict, text: str) -> str:
    """Публикует реплику и ставит Семёну задание ответить."""
    bird = get_bird_user()
    message = MessageService.create_message(
        user_id=bird.id,
        room=room,
        text=text,
    )
    BirdPost.objects.create(
        room=room,
        message=message,
        topic_id=str(topic.get("id", ""))[:64],
    )
    broadcast_message(message)
    enqueue_bartender_job(
        user=bird,
        room=room,
        question=message,
        private=False,
    )
    return text
