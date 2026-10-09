"""Два флажка заменяются выбором из трёх.

Было ``enabled`` (разрешить уведомления) и ``direct_messages_enabled``
(присылать ли личные). Получалось состояние «разрешено, но ни о чём не
сообщать»: человек его выставлял, ничего не приходило, и понять почему
было нельзя. Стало одно поле с тремя значениями.

**Настройки людей переносятся, а не сбрасываются.** Наивная миграция
удалила бы оба поля и выставила всем «только личные» — то есть человек,
выключивший уведомления, после обновления начал бы их получать. Это худшее,
что можно сделать с настройкой приватности: он просил не беспокоить, а его
не послушали.

Порядок поэтому такой: добавить поле, перелить значения, и только потом
удалить старое.
"""

import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


def forwards(apps, schema_editor):
    """Переносит два флажка в один режим."""
    PushSubscription = apps.get_model("users", "PushSubscription")

    # Выключенные остаются выключенными.
    PushSubscription.objects.filter(enabled=False).update(mode="off")

    # Разрешённые, но без личных сообщений: присылать было нечего, значит
    # для человека это тоже «выключено». Придумывать за него «все новые
    # сообщения» нельзя — он такого не просил.
    PushSubscription.objects.filter(enabled=True, direct_messages_enabled=False).update(mode="off")

    PushSubscription.objects.filter(enabled=True, direct_messages_enabled=True).update(mode="direct")


def backwards(apps, schema_editor):
    """Возвращает флажки из режима — на случай отката."""
    PushSubscription = apps.get_model("users", "PushSubscription")

    PushSubscription.objects.filter(mode="off").update(enabled=False, direct_messages_enabled=False)
    PushSubscription.objects.filter(mode="direct").update(enabled=True, direct_messages_enabled=True)
    # «Все новые сообщения» в старой схеме выразить нечем: личные там были
    # единственным видом. Ближайшее честное — включить личные.
    PushSubscription.objects.filter(mode="all").update(enabled=True, direct_messages_enabled=True)


class Migration(migrations.Migration):
    dependencies = [
        ("users", "0013_user_accepted_rules"),
    ]

    operations = [
        # 1. Новое поле появляется рядом со старыми.
        migrations.AddField(
            model_name="pushsubscription",
            name="mode",
            field=models.CharField(
                choices=[
                    ("off", "Выключены"),
                    ("direct", "Только личные сообщения"),
                    ("all", "Все новые сообщения"),
                ],
                default="direct",
                max_length=8,
                verbose_name="Что присылать",
            ),
        ),
        # 2. Значения переливаются.
        migrations.RunPython(forwards, backwards),
        # 3. И только теперь старое уходит.
        migrations.RemoveField(model_name="pushsubscription", name="direct_messages_enabled"),
        migrations.RemoveField(model_name="pushsubscription", name="enabled"),
        migrations.CreateModel(
            name="NotificationSettings",
            fields=[
                (
                    "id",
                    models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID"),
                ),
                (
                    "timezone",
                    models.CharField(
                        blank=True,
                        help_text="Имя часового пояса, например Europe/Moscow. Пусто — пояс проекта.",
                        max_length=64,
                        verbose_name="Часовой пояс",
                    ),
                ),
                ("quiet_from", models.PositiveSmallIntegerField(default=0, verbose_name="Тишина с")),
                ("quiet_to", models.PositiveSmallIntegerField(default=0, verbose_name="Тишина до")),
                (
                    "user",
                    models.OneToOneField(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="notification_settings",
                        to=settings.AUTH_USER_MODEL,
                        verbose_name="Пользователь",
                    ),
                ),
            ],
            options={
                "verbose_name": "Настройки уведомлений",
                "verbose_name_plural": "Настройки уведомлений",
            },
        ),
    ]
