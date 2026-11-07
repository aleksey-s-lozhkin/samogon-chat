import json
import logging
import re
from dataclasses import dataclass
from html import escape
from pathlib import Path
from time import monotonic
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from django.conf import settings
from django.contrib.auth import get_user_model

from chat.services.bartender_context import BIRD_LABEL, BartenderContextEntry
from chat.services.bartender_guardrails import guardrail_reply
from chat.services.language import needs_language_retry
from chat.services.ollama import THINKING_TRACE, chat_payload


#: Режим разговора с персонажем: отдельный файл, а не строка в коде.
#: Базовая персона одна; добавка подключается, только когда говорит персонаж.
#: Проверено на живой модели: без неё Семён повторяет её реплику и предлагает
#: кружку, а с голыми запретами уходит в сюрреализм.
CHARACTER_PROMPTS = {
    label: (
        Path(__file__).with_name("prompts") / name
    ).read_text(encoding="utf-8").strip()
    for label, name in ((BIRD_LABEL, "semen-to-bird.txt"),)
}

#: Параметры генерации для ответов персонажу: меньше фантазии, короче реплика.
CHARACTER_GENERATION = {"temperature": 0.4, "num_predict": 90}

#: Плоские ответы персонажу: «добавить нечего» читается как обида.
FLAT_CHARACTER_REPLIES = frozenset(
    {
        "добавить нечего",
        "нечего добавить",
        "нечего сказать",
        "мне нечего добавить",
    }
)

#: Служебный префикс, который промпт запрещает, а модель иногда ставит.
SERVICE_PREFIX = re.compile(r"^(?:>>>|’>|«>|Семён\s*:)\s*", re.IGNORECASE)

#: Сколько слов в начале ответа считается повтором реплики собеседника.
ECHO_MIN_WORDS = 3

#: Служебные слова, с которых может начинаться копия чужой реплики.
ECHO_LEAD_WORDS = frozenset({"и", "а", "ну", "да", "вот", "так", "это"})

BARTENDER_MENTION = re.compile(r"^@(?:сем[её]н|semen)\b[,:!]?\s*", re.IGNORECASE)
BARTENDER_SYSTEM_PROMPT = (
    Path(__file__).with_name("prompts") / "semen.txt"
).read_text(encoding="utf-8").strip()
BARTENDER_LANGUAGE_FALLBACK = (
    "Поймал сбой в разговорнике. Спросите ещё раз — я уже сверяю словарь."
)
SENTENCE_END = re.compile(r"(?<=[.!?…])(?:\s|$)")
logger = logging.getLogger(__name__)


class BartenderUnavailable(Exception):
    """Ollama недоступна, но основной чат продолжает работать."""


@dataclass(frozen=True)
class BartenderReply:
    text: str


class BartenderService:
    """Тонкая граница между чатом и локальным API Ollama."""

    def is_mentioned(self, text: str) -> bool:
        return bool(BARTENDER_MENTION.match(text))

    def reply(
        self,
        *,
        text: str,
        context: tuple[BartenderContextEntry, ...] = (),
        current_author: str = "guest_1",
    ) -> BartenderReply:
        started_at = monotonic()
        retried_for_language = False
        prompt = (
            BARTENDER_MENTION.sub("", text).strip()
            or "Поздоровайся с гостями."
        )
        guarded_reply = guardrail_reply(prompt)
        if guarded_reply:
            logger.info("bartender_guardrail_reply rule=%s", guarded_reply.rule)
            return BartenderReply(text=guarded_reply.text)

        messages = self._build_messages(
            prompt=prompt,
            context=context,
            current_author=current_author,
        )
        character_reply = current_author in CHARACTER_PROMPTS
        generation = dict(CHARACTER_GENERATION) if character_reply else {}
        if character_reply and settings.OLLAMA_CHARACTER_MODEL:
            # Отдельная модель персонажа: выгружаем сразу после ответа, чтобы
            # не держать общую модель вытесненной у соседей.
            generation["model"] = settings.OLLAMA_CHARACTER_MODEL
            generation["keep_alive"] = settings.OLLAMA_CHARACTER_KEEP_ALIVE
        try:
            content = self._request_reply(messages, **generation)

            if self._needs_language_retry(content):
                # Повторная попытка исправляет смешение языков у локальной модели.
                retried_for_language = True
                content = self._request_reply(
                    [
                        {"role": "system", "content": BARTENDER_SYSTEM_PROMPT},
                        {
                            "role": "user",
                            "content": (
                                "Перепиши свой ответ ниже только грамотным русским языком, "
                                "без иероглифов, английского текста и markdown. Сохрани "
                                "смысл и ответь коротко.\n\n"
                                f"Ответ: {content}"
                            ),
                        },
                    ]
                )

            if self._needs_language_retry(content):
                content = BARTENDER_LANGUAGE_FALLBACK
        except BartenderUnavailable:
            elapsed_ms = int((monotonic() - started_at) * 1000)
            logger.warning(
                "bartender_reply_failed model=%s elapsed_ms=%d",
                settings.OLLAMA_MODEL,
                elapsed_ms,
            )
            raise

        # Логируем только технический результат, не текст и не данные гостя.
        elapsed_ms = int((monotonic() - started_at) * 1000)
        logger.info(
            "bartender_reply_sent model=%s elapsed_ms=%d language_retry=%s",
            generation.get("model") or settings.OLLAMA_MODEL,
            elapsed_ms,
            retried_for_language,
        )

        cleaned = self._strip_markdown_decoration(content)
        if character_reply and _is_unwanted(cleaned):
            # Плоский ответ — повод переспросить, а не публиковать «обиду».
            logger.info("bartender_flat_character_reply")
            try:
                retry = dict(generation, temperature=0.7)
                content = self._request_reply(messages, **retry)
                retried = self._strip_markdown_decoration(content)
                if retried and not _is_unwanted(retried):
                    cleaned = retried
            except BartenderUnavailable:
                pass
        if character_reply:
            # Персонажу не нужно цитировать собеседника: это выглядит как эхо.
            cleaned = self._strip_leading_echo(cleaned, prompt)
        return BartenderReply(text=self._truncate_reply(cleaned))

    @staticmethod
    def _build_messages(*, prompt, context, current_author):
        parts = []
        if context:
            serialized = "\n".join(
                f'<message author="{escape(item.author)}">{escape(item.text)}</message>'
                for item in context
            )
            parts.append(f"<conversation_history>\n{serialized}\n</conversation_history>")
        parts.append(
            f'<current_message author="{escape(current_author)}">'
            f"{escape(prompt)}</current_message>"
        )
        note = CHARACTER_PROMPTS.get(current_author)
        if note:
            parts.append(f"<character_note>\n{note}\n</character_note>")
        return [
            {"role": "system", "content": BARTENDER_SYSTEM_PROMPT},
            {"role": "user", "content": "\n".join(parts)},
        ]

    @staticmethod
    def _strip_leading_echo(content: str, prompt: str) -> str:
        """Убирает начало ответа, дословно повторяющее реплику собеседника.

        Просьба в промпте не помогает: на живой модели ответ регулярно
        начинался с копии фразы птицы. Гость мог бы цитировать намеренно, а
        персонажу это ни к чему, поэтому чистим только для персонажа.
        """
        prompt_words = [_echo_word(word) for word in prompt.split()]
        prompt_words = [word for word in prompt_words if word]
        if len(prompt_words) < ECHO_MIN_WORDS:
            return content

        # Тире и прочая пунктуация — отдельные «слова» после split. Их надо
        # пропускать при сравнении, иначе «Bluetooth — это» не совпадёт с
        # «Bluetooth это» и эхо останется.
        tokens = content.split()
        meaningful = [
            (_echo_word(token), index)
            for index, token in enumerate(tokens)
            if _echo_word(token)
        ]

        # Ответ часто начинается с «И», «А», «Ну» перед копией реплики.
        # Такие слова не должны спасать эхо от обрезки.
        start = 0
        while start < len(meaningful) and meaningful[start][0] in ECHO_LEAD_WORDS:
            start += 1

        matched = 0
        for prompt_word, (content_word, _index) in zip(
            prompt_words, meaningful[start:]
        ):
            if prompt_word != content_word:
                break
            matched += 1

        needed = max(ECHO_MIN_WORDS, len(prompt_words) // 2)
        if matched < needed:
            return content

        next_index = start + matched
        cut_from = (
            meaningful[next_index][1] if next_index < len(meaningful) else len(tokens)
        )
        rest = " ".join(tokens[cut_from:]).lstrip(" \t\n—–-,.:;!?)»\"")
        return rest or content

    @staticmethod
    def _truncate_reply(content: str) -> str:
        """Ограничиваем ответ, не оставляя в чате оборванное слово или фразу."""
        max_length = settings.BARTENDER_RESPONSE_MAX_LENGTH
        if len(content) <= max_length:
            return content

        shortened = content[:max_length].rstrip()
        sentence_ends = [match.end() for match in SENTENCE_END.finditer(shortened)]
        if sentence_ends:
            return shortened[: sentence_ends[-1]].rstrip()

        if " " in shortened:
            shortened = shortened.rsplit(" ", 1)[0].rstrip()
        return f"{shortened}…"

    @staticmethod
    def _strip_markdown_decoration(content: str) -> str:
        """Убираем видимые маркеры, не меняя текст и имена вроде __hash__."""
        content = re.sub(r"```(?:[A-Za-z0-9_+-]+)?\s*", "", content)
        content = content.replace("`", "").replace("**", "").strip()
        # Модель иногда ставит служебный префикс, который промпт запрещает.
        return SERVICE_PREFIX.sub("", content).strip()

    @staticmethod
    def _needs_language_retry(content: str) -> bool:
        """Не публикуем ответы без русской речи или с китайскими символами."""
        return needs_language_retry(content)

    def _request_reply(
        self,
        messages: list[dict[str, str]],
        *,
        temperature: float | None = None,
        num_predict: int | None = None,
        model: str | None = None,
        keep_alive: int | str | None = None,
    ) -> str:
        payload = chat_payload(
            messages,
            temperature=(
                settings.OLLAMA_TEMPERATURE if temperature is None else temperature
            ),
            num_predict=(
                settings.OLLAMA_NUM_PREDICT if num_predict is None else num_predict
            ),
            model=model,
            keep_alive=keep_alive,
        )
        request = Request(
            f"{settings.OLLAMA_BASE_URL.rstrip('/')}/api/chat",
            data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )

        try:
            with urlopen(request, timeout=settings.OLLAMA_TIMEOUT_SECONDS) as response:
                data = json.load(response)
        except (
            HTTPError,
            URLError,
            TimeoutError,
            OSError,
            json.JSONDecodeError,
        ) as error:
            raise BartenderUnavailable from error

        content = data.get("message", {}).get("content")
        if not isinstance(content, str) or not content.strip():
            raise BartenderUnavailable

        # Не отдаём цепочку мыслей ошибочно подключённой reasoning-модели.
        return THINKING_TRACE.sub("", content).strip()

    def get_bartender_user(self):
        user_model = get_user_model()
        user, created = user_model.objects.get_or_create(
            username=settings.BARTENDER_USERNAME,
            defaults={"is_active": False},
        )
        if created:
            # Технический пользователь не должен иметь возможность войти в чат.
            user.set_unusable_password()
            user.save(update_fields=["password"])
        return user


def _is_flat(reply: str) -> bool:
    """Ответ без мысли: «добавить нечего» и его варианты."""
    normalized = re.sub(r"[^\wа-яё ]+", "", reply.lower()).strip()
    return " ".join(normalized.split()) in FLAT_CHARACTER_REPLIES


def _is_unwanted(reply: str) -> bool:
    """Ответ персонажу, который нельзя публиковать.

    Плоский («добавить нечего») — читается как обида. С вопросом — нарушает
    правило «не спрашивай птицу, она не ответит»; проверено на живой 14B:
    она задала вопрос, хотя подсказка это запрещает. Просьба в промпте — не
    механизм, поэтому проверяем кодом.
    """
    return _is_flat(reply) or reply.rstrip().endswith("?")


def _echo_word(word: str) -> str:
    """Слово без регистра и пунктуации — для сравнения «то же самое или нет»."""
    return re.sub(r"[^\wа-яё'-]+", "", word.lower())


bartender = BartenderService()
