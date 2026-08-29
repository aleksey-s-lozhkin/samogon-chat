"""Создаёт безопасные тестовые аккаунты для проверки длинных списков."""

from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand, CommandError


class Command(BaseCommand):
    help = "Создаёт тестовых пользователей для локальной проверки интерфейса."

    def add_arguments(self, parser):
        parser.add_argument(
            "--count",
            type=int,
            default=60,
            help="Сколько аккаунтов создать (по умолчанию 60).",
        )
        parser.add_argument(
            "--password",
            default="demo-chat-password",
            help="Пароль для всех тестовых аккаунтов.",
        )
        parser.add_argument(
            "--prefix",
            default="demo_guest",
            help="Префикс логинов тестовых аккаунтов.",
        )
        parser.add_argument(
            "--allow-production",
            action="store_true",
            help="Явно разрешить запуск вне DEBUG (обычно не нужно).",
        )

    def handle(self, *args, **options):
        if not settings.DEBUG and not options["allow_production"]:
            raise CommandError(
                "Команда заблокирована вне DEBUG. "
                "Не засоряйте production демо-аккаунтами."
            )
        if options["count"] < 1:
            raise CommandError("Количество аккаунтов должно быть положительным.")

        user_model = get_user_model()
        created = 0
        skipped = 0
        for number in range(1, options["count"] + 1):
            username = f'{options["prefix"]}_{number:02d}'
            user, was_created = user_model.objects.get_or_create(
                username=username,
                defaults={"email": f"{username}@example.invalid"},
            )
            if was_created:
                user.set_password(options["password"])
                user.save(update_fields=["password"])
                created += 1
            else:
                skipped += 1

        self.stdout.write(
            self.style.SUCCESS(
                f"Готово: создано {created}, уже существовало {skipped}. "
                "Аккаунты офлайн; живое присутствие проверяется WebSocket-сценарием."
            )
        )
