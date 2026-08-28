from django.contrib.auth import get_user_model
from django.test import TestCase

from .models import Message
from .services.messages import MessageService


User = get_user_model()


class MessageServiceTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            username="alex",
        )

    def test_create_message(self):
        message = MessageService.create_message(
            user_id=self.user.id,
            room_name="general",
            text="Hello, Samogon!",
        )

        self.assertEqual(message.user, self.user)
        self.assertEqual(message.room_name, "general")
        self.assertEqual(message.text, "Hello, Samogon!")

    def test_get_room_messages(self):
        MessageService.create_message(
            user_id=self.user.id,
            room_name="general",
            text="First message",
        )

        MessageService.create_message(
            user_id=self.user.id,
            room_name="other",
            text="Other room",
        )

        messages = MessageService.get_room_messages("general")

        self.assertEqual(len(messages), 1)
        self.assertEqual(messages[0]["username"], "alex")
        self.assertEqual(messages[0]["message"], "First message")
        