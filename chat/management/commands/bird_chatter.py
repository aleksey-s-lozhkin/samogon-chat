"""Один тик Птицы-Говоруна.

Команда вызывается по расписанию (cron или systemd-таймер) и **сама решает**,
пора ли говорить: смотрит гостей в комнате, интервал, тихие часы и дневной
потолок. Так «раз в 30 минут» — это настройка, а не хрупкое расписание:
пропущенный запуск или перезапуск контейнера ничего не ломает.
"""

from django.core.management.base import BaseCommand

from chat.services.bird import decide, last_refusal, speak


class Command(BaseCommand):
    help = "Решает, пора ли птице говорить, и при необходимости говорит."

    def add_arguments(self, parser):
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Показать решение и текст, ничего не записывая в чат.",
        )
        parser.add_argument(
            "--force",
            action="store_true",
            help=(
                "Обойти паузы: интервал, потолок, тихие часы и гостей. "
                "Нужно только для обкатки руками."
            ),
        )

    def handle(self, *args, **options):
        dry_run = options["dry_run"]
        force = options["force"]

        if not force:
            decision = decide()
            if not decision.speak:
                self.stdout.write(f"Молчит: {decision.reason}")
                return

        text = speak(force=force, dry_run=dry_run)
        if text is None:
            self.stdout.write(f"Молчит: {last_refusal() or 'причина неизвестна'}")
            return

        if dry_run:
            self.stdout.write(f"Сказала бы (ничего не записано): {text}")
        else:
            self.stdout.write(f"Сказала: {text}")
