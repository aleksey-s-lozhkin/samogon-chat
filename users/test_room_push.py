"""Уведомления о сообщениях в открытой комнате.

Новый вид уведомлений, и вместе с ним — новый способ раздражать людей.
Здесь проверяются все ограничители сразу: отправителю не шлём, не
согласившимся не шлём, тем, кто и так в приложении, тоже.
"""

from unittest.mock import patch

from django.test import TestCase, override_settings

from chat.models import Message, Room
from users.models import NotificationSettings, PushSubscription, User
from users.services.notifications import in_quiet_hours, local_now
from users.services.push import send_room_message_push


@override_settings(WEB_PUSH_ENABLED=True)
class RoomPushTests(TestCase):
    """Кому уходит уведомление о сообщении в комнате."""

    def setUp(self):
        self.room = Room.objects.create(name="Общая", slug="general")
        self.sender = User.objects.create_user(username="пишущий", password="тайна")
        self.listener = User.objects.create_user(username="слушающий", password="тайна")

    def _subscription(self, user, mode):
        return PushSubscription.objects.create(
            user=user,
            endpoint=f"https://push.example.com/{user.username}-{mode}",
            p256dh="ключ",
            auth="секрет",
            mode=mode,
        )

    def _send(self):
        return send_room_message_push(
            room_slug=self.room.slug,
            room_name=self.room.name,
            sender_id=self.sender.pk,
        )

    @patch("users.services.push.send_push_payload")
    def test_only_all_mode_receives(self, payload):
        """«Только личные» не получает сообщения из общих комнат.

        Это и есть разница между двумя режимами. Если бы сообщения из
        комнат уходили всем, выбор был бы фиктивным.
        """
        from users.services.push import PushDeliveryResult

        payload.return_value = PushDeliveryResult(delivered=1, failed=0, removed=0)
        self._subscription(self.listener, PushSubscription.Mode.DIRECT)

        assert self._send() == 0
        payload.assert_not_called()

    @patch("users.services.push.send_push_payload")
    def test_all_mode_receives(self, payload):
        """«Все новые сообщения» получает."""
        from users.services.push import PushDeliveryResult

        payload.return_value = PushDeliveryResult(delivered=1, failed=0, removed=0)
        self._subscription(self.listener, PushSubscription.Mode.ALL)

        assert self._send() == 1
        payload.assert_called_once()

    @patch("users.services.push.send_push_payload")
    def test_sender_is_not_notified(self, payload):
        """Автор сообщения не получает уведомление о своём же сообщении."""
        from users.services.push import PushDeliveryResult

        payload.return_value = PushDeliveryResult(delivered=1, failed=0, removed=0)
        self._subscription(self.sender, PushSubscription.Mode.ALL)

        assert self._send() == 0
        payload.assert_not_called()

    @patch("users.services.push.send_push_payload")
    def test_online_user_is_skipped(self, payload):
        """Кто сидит в приложении, тому не шлём.

        Главный ограничитель от потока уведомлений: человек видит
        сообщение на экране, и push в этот момент только дёргает телефон.
        """
        from users.services.push import PushDeliveryResult

        payload.return_value = PushDeliveryResult(delivered=1, failed=0, removed=0)
        self._subscription(self.listener, PushSubscription.Mode.ALL)

        with patch("users.services.notifications.is_user_online_sync", return_value=True):
            assert self._send() == 0
        payload.assert_not_called()

    @patch("users.services.push.send_push_payload")
    def test_quiet_hours_are_respected(self, payload):
        """Ночью сообщения из комнаты не будят."""
        from users.services.push import PushDeliveryResult

        payload.return_value = PushDeliveryResult(delivered=1, failed=0, removed=0)
        self._subscription(self.listener, PushSubscription.Mode.ALL)

        hour = local_now(self.listener).hour
        NotificationSettings.objects.update_or_create(
            user=self.listener,
            defaults={"quiet_from": hour, "quiet_to": (hour + 1) % 24},
        )
        assert in_quiet_hours(hour, quiet_from=hour, quiet_to=(hour + 1) % 24) is True

        assert self._send() == 0
        payload.assert_not_called()

    @patch("users.services.push.send_push_payload")
    def test_disabled_mode_receives_nothing(self, payload):
        """Выключенные не получают и сообщений из комнаты."""
        from users.services.push import PushDeliveryResult

        payload.return_value = PushDeliveryResult(delivered=1, failed=0, removed=0)
        self._subscription(self.listener, PushSubscription.Mode.OFF)

        assert self._send() == 0
        payload.assert_not_called()

    @patch("users.services.push.send_push_payload")
    def test_inactive_users_are_skipped(self, payload):
        """Заблокированные и удалённые не получают уведомлений."""
        from users.services.push import PushDeliveryResult

        payload.return_value = PushDeliveryResult(delivered=1, failed=0, removed=0)
        self.listener.is_active = False
        self.listener.save(update_fields=["is_active"])
        self._subscription(self.listener, PushSubscription.Mode.ALL)

        assert self._send() == 0

    @patch("users.services.push.send_push_payload")
    def test_notification_hides_the_message_and_sender(self, payload):
        """В уведомлении нет ни текста сообщения, ни имени автора.

        Его видно на экране блокировки. Текст сообщения и имя отправителя
        туда попадать не должны — это обещано в интерфейсе профиля.
        """
        from users.services.push import PushDeliveryResult

        payload.return_value = PushDeliveryResult(delivered=1, failed=0, removed=0)
        self._subscription(self.listener, PushSubscription.Mode.ALL)
        Message.objects.create(user=self.sender, room=self.room, text="Тайный текст")

        self._send()

        sent = payload.call_args.kwargs["payload"]
        assert "Тайный текст" not in sent["body"]
        assert self.sender.username not in sent["body"]
        assert self.room.name in sent["title"]
