"""Когда человека можно беспокоить уведомлением.

Три причины ответить «нет»: он выбрал не получать такое, у него тихие
часы, или он и так сидит в приложении. Здесь проверяются все три, и
отдельно — переход тихих часов через полночь: это тот случай, который
пишут неправильно, потому что он выглядит как ошибка ввода.
"""

import json
from unittest.mock import patch
from zoneinfo import ZoneInfo

from django.test import TestCase
from django.urls import reverse

from users.models import NotificationSettings, PushSubscription, User
from users.services.notifications import (
    KIND_DIRECT,
    KIND_ROOM,
    allows,
    in_quiet_hours,
    local_now,
    should_notify,
    subscriptions_for,
)


class QuietHoursTests(TestCase):
    """Само правило тишины, без базы и часовых поясов."""

    def test_window_inside_one_day(self):
        """09:00–17:00 — обычное окно внутри суток."""
        assert in_quiet_hours(10, quiet_from=9, quiet_to=17) is True
        assert in_quiet_hours(8, quiet_from=9, quiet_to=17) is False
        assert in_quiet_hours(17, quiet_from=9, quiet_to=17) is False

    def test_window_across_midnight(self):
        """22:00–09:00 — окно через полночь, и это норма, а не опечатка.

        Именно здесь ошибаются чаще всего: пишут условие «с <= час < до»,
        и ночная тишина перестаёт работать ровно тогда, когда нужна.
        """
        assert in_quiet_hours(23, quiet_from=22, quiet_to=9) is True
        assert in_quiet_hours(3, quiet_from=22, quiet_to=9) is True
        assert in_quiet_hours(8, quiet_from=22, quiet_to=9) is True
        assert in_quiet_hours(9, quiet_from=22, quiet_to=9) is False
        assert in_quiet_hours(12, quiet_from=22, quiet_to=9) is False
        assert in_quiet_hours(21, quiet_from=22, quiet_to=9) is False

    def test_equal_hours_mean_no_quiet(self):
        """Окно нулевой длины — «тихих часов нет», а не сутки тишины.

        Прочитать это как «с 0 до 0» значило бы замолчать навсегда.
        """
        assert in_quiet_hours(0, quiet_from=0, quiet_to=0) is False
        assert in_quiet_hours(13, quiet_from=13, quiet_to=13) is False


class LocalTimeTests(TestCase):
    """Часовой пояс человека."""

    def setUp(self):
        self.user = User.objects.create_user(username="пояс", password="тайна")

    def test_defaults_to_project_timezone(self):
        """Без своего пояса берётся пояс проекта."""
        assert local_now(self.user).tzinfo is not None

    def test_uses_the_persons_timezone(self):
        """Свой пояс уважается: у человека может быть не пояс сервера."""
        NotificationSettings.objects.create(user=self.user, timezone="Asia/Tokyo")
        assert local_now(self.user).tzinfo == ZoneInfo("Asia/Tokyo")

    def test_unknown_timezone_falls_back(self):
        """Опечатка в поясе не ломает тихие часы целиком.

        Молча падать из-за неверного имени значило бы отключить тишину
        полностью — то есть разбудить человека ночью из-за опечатки.
        """
        NotificationSettings.objects.create(user=self.user, timezone="Не/Существует")
        assert local_now(self.user).tzinfo is not None


class ShouldNotifyTests(TestCase):
    """Решение по всем трём причинам сразу."""

    def setUp(self):
        self.user = User.objects.create_user(username="гость", password="тайна")

    def _quiet_around_now(self):
        """Включает тишину на текущий местный час.

        Через ``update_or_create``, а не ``create``: ``local_now`` сам
        заводит строку настроек при первом обращении, и вторая попытка
        создать её для того же человека упирается в уникальность. Тест
        не должен зависеть от того, спрашивали время до него или нет.
        """
        hour = local_now(self.user).hour
        NotificationSettings.objects.update_or_create(
            user=self.user,
            defaults={"quiet_from": hour, "quiet_to": (hour + 1) % 24},
        )

    def test_notifies_by_default(self):
        allowed, reason = should_notify(self.user, kind=KIND_DIRECT)
        assert allowed is True
        assert reason

    def test_quiet_hours_block(self):
        """В тихий час не шлём и говорим, почему.

        Окно берётся вокруг текущего местного часа, чтобы тест не зависел
        от того, когда его запустили.
        """
        self._quiet_around_now()

        allowed, reason = should_notify(self.user, kind=KIND_DIRECT)
        assert allowed is False
        assert "тихие часы" in reason

    def test_online_blocks(self):
        """Человек в приложении — уведомление не нужно.

        Он и так видит сообщение. Push в этот момент только дёргает
        телефон, который уже в руке.
        """
        with patch("users.services.notifications.is_user_online_sync", return_value=True):
            allowed, reason = should_notify(self.user, kind=KIND_DIRECT)

        assert allowed is False
        assert "в приложении" in reason

    def test_quiet_hours_win_over_presence(self):
        """В тихий час в Redis даже не ходим.

        Присутствие — единственная проверка, которая обращается наружу;
        при тишине она бессмысленна, и лишний запрос к Redis незачем.
        """
        self._quiet_around_now()

        with patch(
            "users.services.notifications.is_user_online_sync"
        ) as presence:
            allowed, _reason = should_notify(self.user, kind=KIND_DIRECT)

        assert allowed is False
        presence.assert_not_called()

    def test_rejects_unknown_kind(self):
        """Неизвестный вид — ошибка программы, а не повод угадывать."""
        try:
            should_notify(self.user, kind="выдумка")
        except ValueError:
            return
        raise AssertionError("неизвестный вид должен быть отвергнут")


class ModeTests(TestCase):
    """Что попадает в каждый режим."""

    def setUp(self):
        self.user = User.objects.create_user(username="режим", password="тайна")

    def _subscription(self, mode):
        return PushSubscription.objects.create(
            user=self.user,
            endpoint=f"https://push.example.com/{mode}",
            p256dh="ключ",
            auth="секрет",
            mode=mode,
        )

    def test_direct_mode_excludes_rooms(self):
        """«Только личные» не присылает сообщения из общих бесед.

        Иначе человек выбрал бы одно, а получил другое.
        """
        subscription = self._subscription(PushSubscription.Mode.DIRECT)
        assert allows(subscription, kind=KIND_DIRECT) is True
        assert allows(subscription, kind=KIND_ROOM) is False

    def test_all_mode_includes_both(self):
        """«Все новые сообщения» включает и личные, и общие."""
        subscription = self._subscription(PushSubscription.Mode.ALL)
        assert allows(subscription, kind=KIND_DIRECT) is True
        assert allows(subscription, kind=KIND_ROOM) is True

    def test_off_mode_excludes_everything(self):
        """«Выключены» не получает ничего."""
        subscription = self._subscription(PushSubscription.Mode.OFF)
        assert allows(subscription, kind=KIND_DIRECT) is False
        assert allows(subscription, kind=KIND_ROOM) is False

    def test_subscriptions_for_returns_only_matching(self):
        """Выборка отдаёт только те устройства, которым положено."""
        self._subscription(PushSubscription.Mode.OFF)
        self._subscription(PushSubscription.Mode.DIRECT)
        self._subscription(PushSubscription.Mode.ALL)

        rooms = list(subscriptions_for(user=self.user, kind=KIND_ROOM))
        assert [item.mode for item in rooms] == [PushSubscription.Mode.ALL]

        direct = list(subscriptions_for(user=self.user, kind=KIND_DIRECT))
        assert sorted(item.mode for item in direct) == [
            PushSubscription.Mode.ALL,
            PushSubscription.Mode.DIRECT,
        ]

    def test_other_users_are_not_included(self):
        """Чужие подписки в выборку не попадают."""
        other = User.objects.create_user(username="чужой", password="тайна")
        PushSubscription.objects.create(
            user=other,
            endpoint="https://push.example.com/чужой",
            p256dh="ключ",
            auth="секрет",
            mode=PushSubscription.Mode.ALL,
        )
        self._subscription(PushSubscription.Mode.ALL)

        found = subscriptions_for(user=self.user, kind=KIND_ROOM)
        assert all(item.user_id == self.user.pk for item in found)
        assert found.count() == 1


class ProfileUiTests(TestCase):
    """Интерфейс настроек: три режима и тихие часы.

    Тесты держат обещание, что человек видит **выбор из трёх**. Два
    флажка возвращались бы незаметно: разметка поменялась бы, а
    тесты на модель этого не заметили.
    """

    def setUp(self):
        self.user = User.objects.create_user(username="настройки", password="тайна-12345")
        self.client.force_login(self.user)

    def test_profile_offers_all_three_modes(self):
        """На странице профиля есть все три режима."""
        response = self.client.get(reverse("profile"))
        assert response.status_code == 200, f"профиль отдал {response.status_code}"
        page = response.content.decode()
        for mode in ("off", "direct", "all"):
            assert f'value="{mode}"' in page, f"нет режима {mode}"

    def test_profile_has_quiet_hours_fields(self):
        """Поля тихих часов на месте."""
        response = self.client.get(reverse("profile"))
        assert response.status_code == 200, f"профиль отдал {response.status_code}"
        page = response.content.decode()
        assert "data-push-quiet-from" in page
        assert "data-push-quiet-to" in page
        assert "data-push-quiet-save" in page

    def test_quiet_hours_are_saved(self):
        """Сохранённые тихие часы читаются обратно."""
        response = self.client.post(
            reverse("push_quiet_hours"),
            data=json.dumps({"quietFrom": 23, "quietTo": 7, "timezone": "Europe/Moscow"}),
            content_type="application/json",
        )
        assert response.status_code == 200
        saved = NotificationSettings.objects.get(user=self.user)
        assert (saved.quiet_from, saved.quiet_to) == (23, 7)
        assert saved.timezone == "Europe/Moscow"

    def test_quiet_across_midnight_is_reported(self):
        """Сервер говорит, действует ли тишина прямо сейчас.

        Интерфейс показывает это человеку, и «23–7» должно читаться как
        действующее ночью, а не как ошибка ввода.
        """
        response = self.client.post(
            reverse("push_quiet_hours"),
            data=json.dumps({"quietFrom": 0, "quietTo": 23}),
            content_type="application/json",
        )
        assert response.status_code == 200
        assert response.json()["quiet"] is True

    def test_invalid_hour_is_refused(self):
        """Час вне 0–23 отвергается, а не сохраняется как попало."""
        response = self.client.post(
            reverse("push_quiet_hours"),
            data=json.dumps({"quietFrom": 99, "quietTo": 7}),
            content_type="application/json",
        )
        assert response.status_code == 400
        assert NotificationSettings.objects.filter(user=self.user).exists() is False

    def test_quiet_hours_need_login(self):
        """Без входа настройки не сохранить."""
        self.client.logout()
        response = self.client.post(
            reverse("push_quiet_hours"),
            data=json.dumps({"quietFrom": 1, "quietTo": 2}),
            content_type="application/json",
        )
        assert response.status_code in (301, 302, 403)
