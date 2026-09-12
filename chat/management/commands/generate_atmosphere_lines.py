from django.core.management.base import BaseCommand, CommandError

from chat.tasks import generate_atmosphere_line_candidate


class Command(BaseCommand):
    help = "Ставит в очередь генерацию черновиков атмосферных строк."

    def add_arguments(self, parser):
        parser.add_argument("--count", type=int, default=5)

    def handle(self, *args, **options):
        count = options["count"]
        if not 1 <= count <= 20:
            raise CommandError("count должен быть от 1 до 20.")
        for _ in range(count):
            generate_atmosphere_line_candidate.delay()
        self.stdout.write(
            self.style.SUCCESS(
                f"В очередь добавлено заданий: {count}."
            )
        )
