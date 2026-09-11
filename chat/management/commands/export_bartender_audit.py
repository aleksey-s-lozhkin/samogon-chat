import json
import re

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

from chat.models import BartenderJob, Message, Room


EMAIL = re.compile(r"\b[^\s@]+@[^\s@]+\.[^\s@]+\b")
IP_ADDRESS = re.compile(r"(?<!\d)(?:\d{1,3}\.){3}\d{1,3}(?!\d)")
URL = re.compile(r"https?://\S+", re.IGNORECASE)
MENTION = re.compile(r"@[\w-]+(?:\.[\w-]+)*", re.UNICODE)


def redact_text(value: str) -> str:
    """Убирает очевидные идентификаторы, не обещая полной анонимизации текста."""
    value = EMAIL.sub("<email>", value)
    value = IP_ADDRESS.sub("<ip>", value)
    value = URL.sub("<url>", value)

    def replace_mention(match):
        mention = match.group(0)
        if mention.casefold() in {"@семён", "@семен", "@semen"}:
            return "@Семён"
        return "@guest"

    return MENTION.sub(replace_mention, value)


class Command(BaseCommand):
    help = (
        "Выгружает обезличенные метрики обращений к Семёну; "
        "текст добавляется только по явному флагу."
    )

    def add_arguments(self, parser):
        parser.add_argument("--limit", type=int, default=100)
        parser.add_argument("--context-size", type=int, default=6)
        parser.add_argument(
            "--include-content",
            action="store_true",
            help=(
                "Добавляет псевдонимизированные тексты публичных реплик. "
                "Результат всё равно следует считать чувствительным."
            ),
        )

    def handle(self, *args, **options):
        limit = options["limit"]
        context_size = options["context_size"]
        if limit < 1 or context_size < 0:
            raise CommandError("limit должен быть больше нуля, context-size — неотрицательным.")

        jobs = (
            BartenderJob.objects.filter(
                room__visibility=Room.Visibility.PUBLIC,
                private=False,
                status=BartenderJob.Status.SUCCEEDED,
                response__isnull=False,
            )
            .select_related("question", "response")
            .order_by("-created_at")[:limit]
        )

        for sample_number, job in enumerate(reversed(jobs), start=1):
            elapsed_ms = None
            if job.started_at and job.finished_at:
                elapsed_ms = int((job.finished_at - job.started_at).total_seconds() * 1000)

            record = {
                "sample": sample_number,
                "created_at": job.created_at.isoformat(),
                # BartenderJob пока не хранит историческую модель. Не выдаём
                # текущую настройку за модель, которой был создан старый ответ.
                "configured_model_at_export": settings.OLLAMA_MODEL,
                "elapsed_ms": elapsed_ms,
                "question_chars": len(job.question.text),
                "response_chars": len(job.response.text),
                "context_sent_to_model": 0,
            }
            if options["include_content"]:
                context = list(
                    Message.objects.filter(
                        room_id=job.room_id,
                        recipient__isnull=True,
                        hidden_at__isnull=True,
                        created_at__lt=job.question.created_at,
                    )
                    .select_related("user")
                    .order_by("-created_at")[:context_size]
                )
                context.reverse()
                record.update(
                    {
                        "room": "public_room",
                        "question": redact_text(job.question.text),
                        "response": redact_text(job.response.text),
                        "surrounding_context": [
                            {
                                "author": (
                                    "bartender"
                                    if item.user.username == settings.BARTENDER_USERNAME
                                    else "guest"
                                ),
                                "text": redact_text(item.text),
                            }
                            for item in context
                        ],
                    }
                )
            self.stdout.write(json.dumps(record, ensure_ascii=False))
