from django.contrib.auth.models import Group, Permission
from django.core.management.base import BaseCommand, CommandError


class Command(BaseCommand):
    """Создаёт минимальную группу модераторов без выдачи superuser-прав."""

    help = "Создаёт или обновляет группу Moderators."

    permission_codes = (
        ("chat", "view_message"),
        ("chat", "change_message"),
        ("chat", "moderate_message"),
        ("chat", "view_moderationevent"),
        ("users", "view_user"),
        ("users", "change_user"),
    )

    def handle(self, *args, **options):
        permissions = []
        for app_label, codename in self.permission_codes:
            try:
                permissions.append(
                    Permission.objects.get(
                        content_type__app_label=app_label,
                        codename=codename,
                    )
                )
            except Permission.DoesNotExist as error:
                raise CommandError(
                    f"Не найдено право {app_label}.{codename}. "
                    "Сначала выполните миграции."
                ) from error

        group, _ = Group.objects.get_or_create(name="Moderators")
        group.permissions.set(permissions)
        self.stdout.write(self.style.SUCCESS("Группа Moderators готова."))
