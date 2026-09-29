from django.core.management.base import BaseCommand

from chat.models import Attachment, NoteAttachment
from chat.services.attachments import build_image_thumbnail


class Command(BaseCommand):
    """Готовит превью для изображений, загруженных до появления поля.

    Команда идемпотентна: вложения с уже готовым превью пропускаются, поэтому
    её можно запускать повторно после добавления новых файлов.
    """

    help = "Создаёт превью изображений для старых вложений."

    def add_arguments(self, parser):
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Показывает объём работы, ничего не записывая.",
        )

    def handle(self, *args, **options):
        dry_run = options["dry_run"]
        created = 0
        skipped = 0
        failed = 0

        for model in (Attachment, NoteAttachment):
            attachments = model.objects.filter(
                kind=Attachment.Kind.IMAGE,
                thumbnail="",
            )
            for attachment in attachments.iterator():
                attachment.file.open("rb")
                try:
                    thumbnail = build_image_thumbnail(attachment.file)
                finally:
                    attachment.file.close()
                if thumbnail is None:
                    # Файл уже маленький или повреждён: превью не нужно.
                    skipped += 1
                    continue
                if dry_run:
                    created += 1
                    continue
                try:
                    attachment.thumbnail.save(
                        attachment.original_name,
                        thumbnail,
                        save=True,
                    )
                except OSError:
                    failed += 1
                else:
                    created += 1

        mode = "Проверка" if dry_run else "Готово"
        self.stdout.write(
            self.style.SUCCESS(
                f"{mode}: превью создано — {created}; пропущено — {skipped}; "
                f"ошибок записи — {failed}."
            )
        )
