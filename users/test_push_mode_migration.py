"""Перенос настроек уведомлений из двух флажков в один режим.

Тест поднимает **старую** схему, кладёт в неё данные, прогоняет миграцию
вперёд и смотрит, что получилось. Иначе проверить нечем: поля, которое
надо проверить, в нынешней модели уже нет.

Ради чего всё: настройка приватности. Наивная миграция выставила бы всем
«только личные», и человек, **выключивший** уведомления, после обновления
начал бы их получать. Он просил не беспокоить — и его не послушали. Это
заметно не сразу и раздражает сильно.
"""

from django.db import connection
from django.db.migrations.executor import MigrationExecutor
from django.test import TransactionTestCase


class PushModeMigrationTests(TransactionTestCase):
    """Проверяет миграцию 0014 на настоящей схеме."""

    migrate_from = ("users", "0013_user_accepted_rules")
    migrate_to = ("users", "0014_remove_pushsubscription_direct_messages_enabled_and_more")

    def _migrate(self, target):
        executor = MigrationExecutor(connection)
        executor.loader.build_graph()
        executor.migrate([target])
        return executor.loader.project_state([target]).apps

    def _make_subscription(self, apps, *, enabled, direct_messages, endpoint):
        """Кладёт подписку в той схеме, которая сейчас применена."""
        User = apps.get_model("users", "User")
        PushSubscription = apps.get_model("users", "PushSubscription")

        user, _ = User.objects.get_or_create(username=f"перенос-{endpoint}")
        return PushSubscription.objects.create(
            user=user,
            endpoint=f"https://push.example.com/{endpoint}",
            p256dh="ключ",
            auth="секрет",
            enabled=enabled,
            direct_messages_enabled=direct_messages,
        )

    def test_disabled_stays_disabled(self):
        """Выключенные уведомления не включаются сами.

        Главная проверка файла. Человек, который их выключил, не должен
        узнать об обновлении по неожиданному уведомлению.
        """
        old_apps = self._migrate(self.migrate_from)
        subscription = self._make_subscription(
            old_apps, enabled=False, direct_messages=True, endpoint="выкл"
        )

        new_apps = self._migrate(self.migrate_to)
        PushSubscription = new_apps.get_model("users", "PushSubscription")

        assert PushSubscription.objects.get(pk=subscription.pk).mode == "off"

    def test_enabled_direct_becomes_direct(self):
        """Разрешённые личные остаются личными."""
        old_apps = self._migrate(self.migrate_from)
        subscription = self._make_subscription(
            old_apps, enabled=True, direct_messages=True, endpoint="личные"
        )

        new_apps = self._migrate(self.migrate_to)
        PushSubscription = new_apps.get_model("users", "PushSubscription")

        assert PushSubscription.objects.get(pk=subscription.pk).mode == "direct"

    def test_enabled_without_direct_becomes_off(self):
        """«Разрешено, но ни о чём не сообщать» — это выключено.

        Такое состояние давали два флажка, и оно было тупиковым: человек
        ждал уведомлений, а приходить было нечему.
        """
        old_apps = self._migrate(self.migrate_from)
        subscription = self._make_subscription(
            old_apps, enabled=True, direct_messages=False, endpoint="ничего"
        )

        new_apps = self._migrate(self.migrate_to)
        PushSubscription = new_apps.get_model("users", "PushSubscription")

        assert PushSubscription.objects.get(pk=subscription.pk).mode == "off"

    def test_nobody_is_switched_to_all(self):
        """Никого не переводят на «все новые сообщения».

        Самый широкий режим человек должен выбрать сам. Проставить его
        миграцией — значит начать присылать больше, чем он просил.
        """
        old_apps = self._migrate(self.migrate_from)
        for index, (enabled, direct) in enumerate(
            [(False, False), (False, True), (True, False), (True, True)]
        ):
            self._make_subscription(
                old_apps,
                enabled=enabled,
                direct_messages=direct,
                endpoint=f"набор-{index}",
            )

        new_apps = self._migrate(self.migrate_to)
        PushSubscription = new_apps.get_model("users", "PushSubscription")

        assert not PushSubscription.objects.filter(mode="all").exists()
