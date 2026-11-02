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
import re
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from django.conf import settings
from django.contrib.auth import get_user_model
from django.db import transaction
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

#: Темы, на которые птица не высказывается. Промпт просит их не трогать, но
#: просьбу к модели нельзя считать гарантией, поэтому есть проверка на выходе.
UNWANTED = re.compile(
    r"водк|вин[оа]\b|виски|пив[оа]|выпив|алкогол|напит|коньяк|шампанск|бокал"
    r"|рюмк|стопк|политик|религи|церкв|молитв|господ",
    re.IGNORECASE,
)

#: Повторная попытка, если модель смешала языки.
LANGUAGE_RETRY = (
    "Перепиши ответ ниже только грамотным русским языком, без иероглифов и "
    "английского текста. Сохрани смысл, оставь одну короткую фразу.\n\nОтвет: "
)


@dataclass(frozen=True)
class BirdConfig:
    """Действующие настройки: из админки, а если строки нет — из окружения."""

    enabled: bool
    room_slug: str
    interval_minutes: int
    daily_limit: int
    quiet_from: int
    quiet_to: int
    timezone_name: str


def load_config() -> BirdConfig:
    """Читает настройки птицы.

    Строка в админке главнее окружения: переключатель должен работать без
    перезапуска контейнеров. Пока строки нет, берутся значения из окружения —
    так уже настроенный сервис продолжает работать как раньше.
    """
    from chat.models import BirdSettings

    row = BirdSettings.objects.select_related("room").first()
    if row is None:
        quiet_from, quiet_to = quiet_window()
        return BirdConfig(
            enabled=settings.BIRD_ENABLED,
            room_slug=settings.BIRD_ROOM_SLUG.strip(),
            interval_minutes=settings.BIRD_INTERVAL_MINUTES,
            daily_limit=settings.BIRD_DAILY_LIMIT,
            quiet_from=quiet_from,
            quiet_to=quiet_to,
            timezone_name=settings.BIRD_QUIET_TIMEZONE,
        )

    return BirdConfig(
        enabled=row.enabled,
        room_slug=row.room.slug if row.room_id else "",
        interval_minutes=row.interval_minutes,
        daily_limit=row.daily_limit,
        quiet_from=row.quiet_from,
        quiet_to=row.quiet_to,
        timezone_name=row.timezone_name,
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

    if isinstance(payload, list):
        topics = payload
    elif isinstance(payload, dict):
        topics = payload.get("topics")
    else:
        topics = None
    if not isinstance(topics, list):
        logger.warning("bird_topics_invalid path=%s", TOPICS_PATH)
        return []
    def usable(topic) -> bool:
        # fact=None превратилось бы в строку «None» и прошло проверку.
        fact = topic.get("fact") if isinstance(topic, dict) else None
        return isinstance(fact, str) and bool(fact.strip())

    return [topic for topic in topics if usable(topic)]


def load_persona() -> str:
    try:
        return PERSONA_PATH.read_text(encoding="utf-8").strip()
    except OSError:
        logger.warning("bird_persona_unavailable path=%s", PERSONA_PATH)
        return "Ты — Птица-Говорун в баре. Говори по-русски коротко."


def quiet_window() -> tuple[int, int]:
    """Разбирает «1-8» в пару часов. Некорректное значение — без тишины.

    Диапазон проверяется: «8-25» иначе превратилось бы в тишину с 8 до
    полуночи, а «1» без дефиса молча отключило бы тишину целиком. Опечатка в
    настройке не должна менять режим незаметно.
    """
    raw = settings.BIRD_QUIET_HOURS.strip()
    start_text, separator, end_text = raw.partition("-")
    if not separator:
        logger.warning("bird_quiet_hours_invalid value=%s", raw)
        return (0, 0)
    try:
        start, end = int(start_text), int(end_text)
    except ValueError:
        logger.warning("bird_quiet_hours_invalid value=%s", raw)
        return (0, 0)
    if not (0 <= start <= 23 and 0 <= end <= 23):
        logger.warning("bird_quiet_hours_invalid value=%s", raw)
        return (0, 0)
    return (start, end)


def local_now(now: datetime | None = None, *, timezone_name: str | None = None) -> datetime:
    """Время в эталонном поясе птицы.

    Комната общая: «ночь для каждого гостя» не определить, поэтому тихие часы
    считаются по одному поясу. Пояс задаётся в админке, по умолчанию — из
    окружения, и это записано в документации.
    """
    moment = now or timezone.now()
    name = timezone_name or settings.BIRD_QUIET_TIMEZONE
    try:
        zone = ZoneInfo(name)
    except (ZoneInfoNotFoundError, ValueError):
        logger.warning("bird_timezone_unknown value=%s", name)
        zone = ZoneInfo("UTC")
    return moment.astimezone(zone)


#: Значение по умолчанию: «спросить присутствие самому».
FETCH_PRESENCE = object()


def guests_in_room(room_slug: str, *, room_users=FETCH_PRESENCE) -> list[str] | None:
    """Гости комнаты: персонажи и суперпользователи гостями не считаются.

    Суперпользователь — это хозяин заведения: его присутствие не должно
    выключать птицу, иначе он никогда не увидит разговор живьём. Правило
    совпадает с `eligible_guests()`, где суперпользователи тоже не гости.

    **None означает «не знаю»** — присутствие недоступно. Пустой список
    означает «в комнате действительно никого». Путать их нельзя: во втором
    случае птица говорит, в первом обязана молчать.
    """
    if room_users is FETCH_PRESENCE:
        room_users = room_users_sync(room_slug)
    if room_users is None:
        return None

    # Присутствие хранит соединение, а не человека: одна вкладка на гостя.
    names = list(dict.fromkeys(room_users))
    names = [name for name in names if name not in bot_usernames()]
    if not names:
        return []

    owners = set(
        get_user_model().objects.filter(
            username__in=names,
            is_superuser=True,
        ).values_list("username", flat=True)
    )
    return [name for name in names if name not in owners]


class BirdIdentityError(RuntimeError):
    """Имя персонажа занято живым аккаунтом — говорить от его лица нельзя."""


def get_bird_user():
    """Технический пользователь птицы.

    Неактивный и без пароля: под ним нельзя войти, но его реплики — обычные
    сообщения, видимые в комнате.

    ``defaults`` в get_or_create срабатывает только при создании, поэтому
    существующий аккаунт проверяется отдельно. Иначе после смены
    ``BIRD_USERNAME`` на имя живого гостя птица заговорила бы от его лица, а
    при совпадении с Семёном — от лица бармена.
    """
    if settings.BIRD_USERNAME == settings.BARTENDER_USERNAME:
        raise BirdIdentityError("имя птицы совпадает с именем бармена")

    user_model = get_user_model()
    user, created = user_model.objects.get_or_create(
        username=settings.BIRD_USERNAME,
        defaults={"is_active": False},
    )
    if created:
        user.set_unusable_password()
        user.save(update_fields=["password"])
    elif user.is_active or user.has_usable_password():
        raise BirdIdentityError("имя птицы занято активным аккаунтом")
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


def decide(*, now=None, room_users=FETCH_PRESENCE, config=None) -> Decision:
    """Стоит ли говорить прямо сейчас и почему именно так."""
    config = config or load_config()
    if not config.enabled:
        return Decision(False, "выключено переключателем в админке")

    slug = config.room_slug.strip()
    if not slug:
        return Decision(False, "не задана беседа для птицы")

    room = Room.objects.filter(slug=slug).first()
    if room is None:
        return Decision(False, f"комната {slug!r} не найдена")

    guests = guests_in_room(slug, room_users=room_users)
    if guests is None:
        # «Не знаю» — не «никого нет»: молчим, чтобы не говорить поверх гостей.
        return Decision(False, "присутствие недоступно: неизвестно, есть ли гости")
    if guests:
        return Decision(False, f"в комнате гости: {len(guests)}")

    moment = local_now(now, timezone_name=config.timezone_name)
    if in_quiet_hours(
        moment.hour,
        quiet_from=config.quiet_from,
        quiet_to=config.quiet_to,
    ):
        return Decision(False, f"тихий час ({moment.hour}:00)")

    pause = _pause_reason(room, now=now, moment=moment, config=config)
    if pause:
        return Decision(False, pause)

    return Decision(True, "в комнате никого, пора говорить")


def _pause_reason(
    room: Room,
    *,
    now=None,
    moment: datetime | None = None,
    config: BirdConfig | None = None,
) -> str:
    """Почему нельзя говорить из-за ритма: пауза после реплики или потолок.

    Вынесено отдельно, потому что тот же вопрос задаётся второй раз под
    блокировкой строки беседы: пока модель писала реплику, параллельный запуск
    (например, пересечение двух тиков cron) мог успеть сказать своё.
    """
    last = (
        BirdPost.objects.filter(room=room)
        .order_by("-created_at")
        .values_list("created_at", flat=True)
        .first()
    )
    config = config or load_config()
    if last is not None:
        elapsed = (now or timezone.now()) - last
        if elapsed < timedelta(minutes=config.interval_minutes):
            return (
                f"рано: прошло {int(elapsed.total_seconds() // 60)} мин "
                f"из {config.interval_minutes}"
            )

    moment = moment or local_now(now, timezone_name=config.timezone_name)
    day_start = moment.replace(hour=0, minute=0, second=0, microsecond=0)
    said_today = BirdPost.objects.filter(
        room=room,
        created_at__gte=day_start,
    ).count()
    if said_today >= config.daily_limit:
        return f"дневной потолок: {said_today}"
    return ""


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


def rewrite_in_russian(answer: str, *, ask=None) -> str:
    """Просит модель переписать уже полученный ответ по-русски."""
    request = (ask or send_chat)(
        chat_payload(
            [
                {"role": "system", "content": load_persona()},
                {"role": "user", "content": f"{LANGUAGE_RETRY}{answer}"},
            ],
            temperature=BIRD_TEMPERATURE,
            num_predict=BIRD_NUM_PREDICT,
        )
    )
    return request.strip().strip('"').strip()


def _acceptable(text: str) -> bool:
    return bool(text) and len(text) <= settings.BIRD_MAX_LENGTH


def speak(
    *,
    force=False,
    dry_run=False,
    now=None,
    room_users=FETCH_PRESENCE,
    ask=None,
    rng=None,
    config=None,
):
    """Говорит, если пора. Возвращает текст реплики или None.

    ``force`` обходит паузы (интервал, потолок, тишину, гостей) — для обкатки
    руками. Выключенная настройка и незаданная комната не обходятся никогда:
    иначе однажды птица заговорит там, где её не ждали.

    ``room_users`` по умолчанию означает «спросить присутствие самому».
    Явный список — «вот кто в комнате», явный ``None`` — «неизвестно, и тогда
    молчим». Раньше по умолчанию стоял ``None``, и штатный путь без ``--force``
    молчал всегда: путались «не знаю» и «спроси».
    """
    config = config or load_config()
    if force:
        if not config.enabled:
            return _refuse("выключено переключателем в админке")
        slug = config.room_slug.strip()
        if not slug:
            return _refuse("не задана беседа для птицы")
        room = Room.objects.filter(slug=slug).first()
        if room is None:
            return _refuse(f"беседа {slug!r} не найдена")
    else:
        decision = decide(now=now, room_users=room_users, config=config)
        if not decision.speak:
            return _refuse(decision.reason)
        room = Room.objects.get(slug=config.room_slug.strip())

    topic = pick_topic(room=room, rng=rng)
    if topic is None:
        return _refuse("нет поводов: файл bird-topics.json пуст или недоступен")

    try:
        text = compose_line(topic, ask=ask)
    except OllamaUnavailable:
        return _refuse("модель недоступна")

    if needs_language_retry(text):
        # Просим переписать именно прошлый ответ: повтор того же промпта даёт
        # тот же результат и не исправляет смешение языков.
        try:
            text = rewrite_in_russian(text, ask=ask)
        except OllamaUnavailable:
            return _refuse("модель недоступна")
        if needs_language_retry(text):
            return _refuse("модель ответила не по-русски")

    if not _acceptable(text):
        return _refuse("реплика пустая или слишком длинная")

    if UNWANTED.search(text):
        return _refuse("реплика задела нежелательную тему")

    logger.info(
        "bird_speaks topic=%s length=%d room=%s force=%s",
        topic.get("id"),
        len(text),
        room.slug,
        force,
    )
    if dry_run:
        return text

    try:
        return _publish(
            room=room,
            topic=topic,
            text=text,
            force=force,
            now=now,
            room_users=room_users,
            config=config,
        )
    except BirdIdentityError as error:
        return _refuse(f"имя персонажа недоступно: {error}")


def _refuse(reason: str):
    logger.info("bird_silent reason=%s", reason)
    return None


def _publish(
    *,
    room: Room,
    topic: dict,
    text: str,
    force: bool = False,
    now=None,
    room_users=FETCH_PRESENCE,
    config=None,
):
    """Публикует реплику и ставит Семёну задание ответить.

    Строка беседы блокируется на время записи: два пересекающихся запуска не
    должны сказать по реплике каждый. Модель к этому моменту уже отработала,
    поэтому блокировка держится миллисекунды, а не секунды генерации.

    Под блокировкой всё проверяется ещё раз. Генерация занимает секунды, и за
    это время в комнату мог зайти гость или начаться тихий час — тогда реплика
    отменяется, а не публикуется «потому что уже решили».
    """
    config = config or load_config()
    bird = get_bird_user()
    with transaction.atomic():
        Room.objects.select_for_update().get(pk=room.pk)

        if not force:
            pause = _pause_reason(room, now=now, config=config)
            if pause:
                return _refuse(f"передумала: {pause}")

            guests = guests_in_room(room.slug, room_users=room_users)
            if guests is None:
                return _refuse("передумала: присутствие стало недоступно")
            if guests:
                return _refuse(f"передумала: в комнате гости ({len(guests)})")

            moment = local_now(now, timezone_name=config.timezone_name)
            if in_quiet_hours(
                moment.hour,
                quiet_from=config.quiet_from,
                quiet_to=config.quiet_to,
            ):
                return _refuse("передумала: начался тихий час")

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
        # Задание Семёну создаётся в той же транзакции: если оно не создастся,
        # не будет и реплики, а не наоборот. Отправка задания в брокер
        # откладывается до коммита внутри enqueue_bartender_job.
        enqueue_bartender_job(
            user=bird,
            room=room,
            question=message,
            private=False,
        )

    try:
        broadcast_message(message)
    except Exception:
        # Реплика и задание уже сохранены: сбой рассылки не должен отменять
        # разговор, о нём достаточно предупредить.
        logger.warning("bird_broadcast_failed message_id=%s", message.pk, exc_info=True)
    return text
