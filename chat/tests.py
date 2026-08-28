from django.contrib.auth import get_user_model
from django.test import TestCase

from .models import Message, Room
from .services.messages import MessageService


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