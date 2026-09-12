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

from chat.services.bartender_context import BartenderContextEntry
from chat.services.bartender_guardrails import guardrail_reply


BARTENDER_MENTION = re.compile(r"^@(?:сем[её]н|semen)\b[,:!]?\s*", re.IGNORECASE)
HAN_CHARACTERS = re.compile(r"[\u3400-\u9fff]")
CYRILLIC_CHARACTERS = re.compile(r"[А-Яа-яЁё]")
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
        try:
            content = self._request_reply(messages)

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
            settings.OLLAMA_MODEL,
            elapsed_ms,
            retried_for_language,
        )

        return BartenderReply(
            text=self._truncate_reply(self._strip_markdown_decoration(content))
        )

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
        return [
            {"role": "system", "content": BARTENDER_SYSTEM_PROMPT},
            {"role": "user", "content": "\n".join(parts)},
        ]

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
        return content.replace("`", "").replace("**", "").strip()

    @staticmethod
    def _needs_language_retry(content: str) -> bool:
        """Не публикуем ответы без русской речи или с китайскими символами."""
        return bool(HAN_CHARACTERS.search(content)) or not bool(
            CYRILLIC_CHARACTERS.search(content)
        )

    def _request_reply(self, messages: list[dict[str, str]]) -> str:
        payload = {
            "model": settings.OLLAMA_MODEL,
            "stream": False,
            "think": False,
            "keep_alive": settings.OLLAMA_KEEP_ALIVE,
            "options": {
                "temperature": settings.OLLAMA_TEMPERATURE,
                "num_ctx": 4096,
                "num_predict": settings.OLLAMA_NUM_PREDICT,
            },
            "messages": messages,
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
        return re.sub(r"<think>.*?</think>\s*", "", content, flags=re.DOTALL).strip()

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


bartender = BartenderService()
