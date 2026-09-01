from django.core.management.base import BaseCommand

from chat.models import Note
from chat.services.messages import MessageService


class Command(BaseCommand):
    help = "Копирует вложения из старых сохранённых реплик в личные заметки."

    def add_arguments(self, parser):
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Показывает, какие заметки будут дополнены, ничего не меняя.",
        )

    def handle(self, *args, **options):
        dry_run = options["dry_run"]
        copied_notes = 0
        skipped_notes = 0

        notes = Note.objects.exclude(source_message__isnull=True).select_related(
            "source_message"
        ).prefetch_related("attachments", "source_message__attachments")
        for note in notes:
            if note.attachments.exists() or not note.source_message.attachments.exists():
                skipped_notes += 1
                continue

            copied_notes += 1
            if not dry_run:
                MessageService.copy_note_attachments(
                    note=note,
                    source_message=note.source_message,
                )

        mode = "Проверка" if dry_run else "Готово"
        self.stdout.write(
            self.style.SUCCESS(
                f"{mode}: дополнено заметок — {copied_notes}; пропущено — {skipped_notes}."
            )
        )
