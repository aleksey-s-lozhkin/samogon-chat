import json

from asgiref.sync import async_to_sync
from channels.routing import URLRouter
from channels.testing import WebsocketCommunicator
from django.contrib.auth import get_user_model
from django.contrib.auth.models import AnonymousUser
from django.conf import settings
from django.test import TestCase, TransactionTestCase
from django.urls import reverse
from django.utils import timezone
from unittest.mock import patch

from .models import Message, Room, RoomMembership, RoomReadState
from .routing import websocket_urlpatterns
from .services.messages import MessageService
from .services.bartender import BARTENDER_LANGUAGE_FALLBACK, bartender
from .validators import validate_message


User = get_user_model()


class MessageServiceTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            username="alex",
        )

        self.room = Room.objects.create(
            name="General",
            slug="general",
            description="General chat",
        )

    def test_create_message(self):
        message = MessageService.create_message(
            user_id=self.user.id,
            room=self.room,
            text="Hello, Samogon!",
        )

        self.assertEqual(message.user, self.user)
        self.assertEqual(message.room, self.room)
        self.assertEqual(message.text, "Hello, Samogon!")

    def test_get_room_messages(self):
        MessageService.create_message(
            user_id=self.user.id,
            room=self.room,
            text="First message",
        )

        other_room = Room.objects.create(
            name="Other",
            slug="other",
        )

        MessageService.create_message(
            user_id=self.user.id,
            room=other_room,
            text="Other room",
        )

        messages = MessageService.get_room_messages(self.room)

        self.assertEqual(len(messages), 1)
        self.assertEqual(messages[0]["username"], "alex")
        self.assertEqual(messages[0]["message"], "First message")

    def test_get_room_messages_returns_latest_messages_in_chronological_order(self):
        for index in range(3):
            MessageService.create_message(
                user_id=self.user.id,
                room=self.room,
                text=f"Message {index}",
            )

        messages = MessageService.get_room_messages(self.room, limit=2)

        self.assertEqual(
            [message["message"] for message in messages],
            ["Message 1", "Message 2"],
        )

    def test_private_messages_are_visible_only_to_sender_and_recipient(self):
        recipient = User.objects.create_user(username="maria")
        outsider = User.objects.create_user(username="ivan")
        MessageService.create_message(
            user_id=self.user.id,
            room=self.room,
            text="Для всех",
        )
        MessageService.create_message(
            user_id=self.user.id,
            room=self.room,
            text="Только для Марии",
            recipient_id=recipient.id,
        )

        sender_messages = MessageService.get_room_messages(
            self.room,
            viewer_id=self.user.id,
        )
        recipient_messages = MessageService.get_room_messages(
            self.room,
            viewer_id=recipient.id,
        )
        outsider_messages = MessageService.get_room_messages(
            self.room,
            viewer_id=outsider.id,
        )

        self.assertEqual([message["message"] for message in sender_messages], ["Для всех", "Только для Марии"])
        self.assertEqual([message["message"] for message in recipient_messages], ["Для всех", "Только для Марии"])
        self.assertEqual([message["message"] for message in outsider_messages], ["Для всех"])

    def test_hidden_message_is_not_returned_to_chat(self):
        visible = MessageService.create_message(
            user_id=self.user.id,
            room=self.room,
            text="Видимое сообщение",
        )
        hidden = MessageService.create_message(
            user_id=self.user.id,
            room=self.room,
            text="Скрытое сообщение",
        )
        hidden.hidden_at = timezone.now()
        hidden.save(update_fields=("hidden_at",))

        messages = MessageService.get_room_messages(self.room)

        self.assertEqual([message["message"] for message in messages], [visible.text])

    def test_unread_count_uses_only_messages_addressed_to_user_in_public_room(self):
        sender = User.objects.create_user(username="maria")
        RoomReadState.objects.create(
            room=self.room,
            user=self.user,
            last_read_at=timezone.now(),
        )
        MessageService.create_message(
            user_id=sender.id,
            room=self.room,
            text="Общее сообщение",
        )
        MessageService.create_message(
            user_id=sender.id,
            room=self.room,
            text="Лично для Алекса",
            recipient_id=self.user.id,
        )

        unread_count = MessageService.get_unread_count(
            room=self.room,
            user_id=self.user.id,
        )

        self.assertEqual(unread_count, 1)

    def test_unread_count_in_private_room_includes_messages_from_other_members(self):
        guest = User.objects.create_user(username="maria")
        private_room = Room.objects.create(
            name="Тайный столик",
            slug="taynyy-stolik",
            visibility=Room.Visibility.PRIVATE,
            owner=self.user,
        )
        RoomMembership.objects.bulk_create(
            [
                RoomMembership(room=private_room, user=self.user),
                RoomMembership(room=private_room, user=guest),
            ]
        )
        RoomReadState.objects.create(
            room=private_room,
            user=self.user,
            last_read_at=timezone.now(),
        )
        MessageService.create_message(
            user_id=guest.id,
            room=private_room,
            text="Только для своих",
        )

        unread_count = MessageService.get_unread_count(
            room=private_room,
            user_id=self.user.id,
        )

        self.assertEqual(unread_count, 1)


class PrivateRoomViewsTests(TestCase):
    def setUp(self):
        self.owner = User.objects.create_user(username="alex")
        self.first_guest = User.objects.create_user(username="maria")
        self.second_guest = User.objects.create_user(username="ivan")

    def test_owner_can_create_one_private_room_for_up_to_two_guests(self):
        self.client.force_login(self.owner)

        response = self.client.post(
            reverse("chat:create_private_room"),
            {
                "name": "Сообразим на троих",
                "members": [self.first_guest.id, self.second_guest.id],
            },
        )

        room = Room.objects.get(owner=self.owner)
        self.assertRedirects(response, reverse("chat:chat", args=[room.slug]))
        self.assertTrue(room.is_private)
        self.assertEqual(
            set(room.members.values_list("username", flat=True)),
            {"alex", "maria", "ivan"},
        )

    def test_private_room_is_hidden_from_uninvited_guest(self):
        outsider = User.objects.create_user(username="outsider")
        room = Room.objects.create(
            name="Тайный столик",
            slug="taynyy-stolik",
            visibility=Room.Visibility.PRIVATE,
            owner=self.owner,
        )
        RoomMembership.objects.create(room=room, user=self.owner)
        self.client.force_login(outsider)

        response = self.client.get(reverse("chat:chat", args=[room.slug]))

        self.assertEqual(response.status_code, 404)

    def test_different_owners_can_use_the_same_private_room_name(self):
        another_owner = User.objects.create_user(username="petr")
        Room.objects.create(
            name="Сообразим на троих",
            slug="soobrazim-na-troih-alex",
            visibility=Room.Visibility.PRIVATE,
            owner=self.owner,
        )
        self.client.force_login(another_owner)

        response = self.client.post(
            reverse("chat:create_private_room"),
            {"name": "Сообразим на троих", "members": [self.first_guest.id]},
        )

        self.assertEqual(response.status_code, 302)
        self.assertEqual(
            Room.objects.filter(
                name="Сообразим на троих",
                visibility=Room.Visibility.PRIVATE,
            ).count(),
            2,
        )

    def test_private_room_requires_at_least_one_guest(self):
        self.client.force_login(self.owner)

        response = self.client.post(
            reverse("chat:create_private_room"),
            {"name": "Пустой столик"},
        )

        self.assertEqual(response.status_code, 400)
        self.assertContains(
            response,
            "Позовите хотя бы одного гостя",
            status_code=400,
        )


class ChatLayoutViewsTests(TestCase):
    """Проверяет опорные элементы адаптивной раскладки чата."""

    def setUp(self):
        self.user = User.objects.create_user(username="alex")
        self.room = Room.objects.create(
            name="Тестовая раскладка",
            slug="layout-test-room",
        )

    def test_chat_has_separate_people_panel_and_bartender_trigger(self):
        self.client.force_login(self.user)

        response = self.client.get(reverse("chat:chat", args=[self.room.slug]))

        self.assertContains(response, 'class="rooms-sidebar"')
        self.assertContains(response, 'class="presence-sidebar"')
        self.assertContains(response, 'id="bartender-trigger"')

    def test_robots_disallows_indexing_closed_beta(self):
        response = self.client.get(reverse("robots"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Disallow: /")


class MessageValidationTests(TestCase):
    def test_accepts_trimmed_message(self):
        message, error = validate_message('{"message": "  Привет  "}')

        self.assertEqual(message, "Привет")
        self.assertIsNone(error)

    def test_rejects_invalid_payloads(self):
        cases = [
            ("not json", "Некорректный JSON"),
            ("[]", "Сообщение должно быть JSON-объектом"),
            ('{"message": 1}', "Поле message должно быть строкой"),
            ('{"message": "   "}', "Сообщение не может быть пустым"),
            (
                '{"message": "' + "a" * 1001 + '"}',
                "Сообщение не может быть длиннее 1000 символов",
            ),
        ]

        for payload, expected_error in cases:
            with self.subTest(payload=payload[:30]):
                message, error = validate_message(payload)
                self.assertIsNone(message)
                self.assertEqual(error, expected_error)


class BartenderServiceTests(TestCase):
    def test_bartender_mention_supports_cyrillic_name(self):
        self.assertTrue(bartender.is_mentioned("@Семён, помоги с логом"))
        self.assertTrue(bartender.is_mentioned("@семен привет"))
        self.assertFalse(bartender.is_mentioned("Семён, помоги с логом"))

    @patch("chat.services.bartender.urlopen")
    def test_reply_uses_ollama_and_removes_thinking_trace(self, mock_urlopen):
        mock_urlopen.return_value.__enter__.return_value.read.return_value = (
            '{"message": {"content": "<think>hidden</think>\\nГотово, лог посмотрел."}}'.encode()
        )

        reply = bartender.reply(
            room_name="Python",
            username="alex",
            text="@Семён, помоги с логом",
        )

        self.assertEqual(reply.text, "Готово, лог посмотрел.")
        request = mock_urlopen.call_args.args[0]
        payload = json.loads(request.data)
        self.assertEqual(payload["model"], settings.OLLAMA_MODEL)
        self.assertFalse(payload["think"])
        self.assertEqual(payload["keep_alive"], -1)
        self.assertEqual(payload["options"]["temperature"], 0.5)
        self.assertEqual(payload["options"]["num_predict"], 80)
        self.assertIn("Гость @alex", payload["messages"][1]["content"])

    @patch("chat.services.bartender.urlopen")
    def test_reply_retries_when_model_mixes_in_chinese_characters(self, mock_urlopen):
        mock_urlopen.return_value.__enter__.return_value.read.side_effect = [
            '{"message": {"content": "Помогу 指出错误."}}'.encode(),
            '{"message": {"content": "Помогу найти ошибку."}}'.encode(),
        ]

        reply = bartender.reply(room_name="Python", username="alex", text="@Семён помоги")

        self.assertEqual(reply.text, "Помогу найти ошибку.")
        self.assertEqual(mock_urlopen.call_count, 2)

    @patch("chat.services.bartender.urlopen")
    def test_reply_retries_when_model_replies_only_in_english(self, mock_urlopen):
        mock_urlopen.return_value.__enter__.return_value.read.side_effect = [
            '{"message": {"content": "I can only answer in Russian."}}'.encode(),
            '{"message": {"content": "Отвечу по-русски, без лишнего шума."}}'.encode(),
        ]

        reply = bartender.reply(room_name="Python", username="alex", text="@Семён помоги")

        self.assertEqual(reply.text, "Отвечу по-русски, без лишнего шума.")
        self.assertEqual(mock_urlopen.call_count, 2)

    @patch("chat.services.bartender.urlopen")
    def test_reply_uses_safe_fallback_after_second_mixed_language_reply(self, mock_urlopen):
        mock_urlopen.return_value.__enter__.return_value.read.side_effect = [
            '{"message": {"content": "Помогу 指出错误."}}'.encode(),
            '{"message": {"content": "仍然不 по-русски."}}'.encode(),
        ]

        reply = bartender.reply(room_name="Python", username="alex", text="@Семён помоги")

        self.assertEqual(reply.text, BARTENDER_LANGUAGE_FALLBACK)


class ChatConsumerTests(TransactionTestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="alex")
        self.room = Room.objects.create(name="General", slug="general")
        self.application = URLRouter(websocket_urlpatterns)

    def test_anonymous_user_is_rejected_before_accepting_connection(self):
        async_to_sync(self._assert_anonymous_user_is_rejected)()

    def test_banned_user_is_rejected_before_accepting_connection(self):
        self.user.banned_at = timezone.now()
        self.user.save(update_fields=("banned_at",))

        async_to_sync(self._assert_banned_user_is_rejected)()

    def test_uninvited_user_cannot_connect_to_private_room(self):
        outsider = User.objects.create_user(username="maria")
        private_room = Room.objects.create(
            name="Тайный столик",
            slug="taynyy-stolik",
            visibility=Room.Visibility.PRIVATE,
            owner=self.user,
        )
        RoomMembership.objects.create(room=private_room, user=self.user)

        async_to_sync(self._assert_private_room_is_rejected)(outsider)

    async def _assert_private_room_is_rejected(self, outsider):
        communicator = WebsocketCommunicator(
            self.application,
            "/ws/chat/taynyy-stolik/",
        )
        communicator.scope["user"] = outsider

        connected, close_code = await communicator.connect()

        self.assertFalse(connected)
        self.assertEqual(close_code, 4403)

    async def _assert_anonymous_user_is_rejected(self):
        communicator = WebsocketCommunicator(self.application, "/ws/chat/general/")
        communicator.scope["user"] = AnonymousUser()

        connected, close_code = await communicator.connect()

        self.assertFalse(connected)
        self.assertEqual(close_code, 4401)

    async def _assert_banned_user_is_rejected(self):
        communicator = WebsocketCommunicator(self.application, "/ws/chat/general/")
        communicator.scope["user"] = self.user

        connected, close_code = await communicator.connect()

        self.assertFalse(connected)
        self.assertEqual(close_code, 4403)

    def test_authenticated_user_receives_history_and_invalid_message_error(self):
        history_message = Message.objects.create(
            room=self.room,
            user=self.user,
            text="Earlier",
        )

        async_to_sync(self._assert_history_validation_and_delivery)(
            history_message.created_at.isoformat(),
        )
        self.assertTrue(
            Message.objects.filter(room=self.room, user=self.user, text="New message").exists(),
        )

    async def _assert_history_validation_and_delivery(self, created_at):
        communicator = WebsocketCommunicator(self.application, "/ws/chat/general/")
        communicator.scope["user"] = self.user

        connected, _ = await communicator.connect()
        self.assertTrue(connected)
        self.assertEqual(
            await communicator.receive_json_from(),
            {
                "type": "history",
                "messages": [
                    {
                        "username": "alex",
                        "avatar_url": None,
                        "message": "Earlier",
                        "created_at": created_at,
                        "recipient": None,
                        "private": False,
                        "color": "amber",
                    }
                ],
            },
        )
        self.assertEqual(
            await communicator.receive_json_from(),
            {"type": "online_users", "users": ["alex"]},
        )
        presence_message = await communicator.receive_json_from()
        self.assertEqual(presence_message["type"], "user_presence")
        self.assertEqual(
            presence_message["users"],
            [{"username": "alex", "avatar_url": None}],
        )
        self.assertEqual(presence_message["online"], ["alex"])

        await communicator.send_to(text_data="not json")
        self.assertEqual(
            await communicator.receive_json_from(),
            {"type": "error", "message": "Некорректный JSON"},
        )

        await communicator.send_json_to({"message": "New message"})
        delivered_message = await communicator.receive_json_from()
        self.assertEqual(delivered_message["type"], "message")
        self.assertEqual(delivered_message["username"], "alex")
        self.assertIsNone(delivered_message["avatar_url"])
        self.assertEqual(delivered_message["message"], "New message")
        self.assertFalse(delivered_message["private"])
        self.assertEqual(delivered_message["color"], "amber")
        self.assertIn("timestamp", delivered_message)

        await communicator.disconnect()
