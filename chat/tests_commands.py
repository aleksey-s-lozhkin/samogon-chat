import json
from datetime import timedelta
from io import StringIO

from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.test import TestCase, override_settings
from django.utils import timezone

from chat.models import BartenderJob, Message, Room


User = get_user_model()


class ExportBartenderAuditTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="alex@example.com")
        self.bartender = User.objects.create_user(username="semen")
        self.room = Room.objects.create(name="General", slug="general")

    def create_job(self, *, private=False, room=None):
        room = room or self.room
        previous = Message.objects.create(
            user=self.user,
            room=room,
            text="Посмотри https://example.test и напиши @alex",
        )
        question = Message.objects.create(
            user=self.user,
            room=room,
            text="@Семён проверь 192.0.2.10, почта me@example.test",
            recipient=self.bartender if private else None,
        )
        response = Message.objects.create(
            user=self.bartender,
            room=room,
            text="Проверю адрес и отвечу @alex.",
            recipient=self.user if private else None,
        )
        started_at = timezone.now()
        return BartenderJob.objects.create(
            user=self.user,
            room=room,
            question=question,
            response=response,
            private=private,
            status=BartenderJob.Status.SUCCEEDED,
            started_at=started_at,
            finished_at=started_at + timedelta(milliseconds=250),
        ), previous

    @override_settings(OLLAMA_MODEL="test-model")
    def test_default_export_contains_metrics_without_message_content(self):
        self.create_job()
        output = StringIO()

        call_command("export_bartender_audit", stdout=output)

        record = json.loads(output.getvalue())
        self.assertEqual(record["configured_model_at_export"], "test-model")
        self.assertEqual(record["elapsed_ms"], 250)
        self.assertIsNone(record["context_sent_to_model"])
        self.assertNotIn("question", record)
        self.assertNotIn("response", record)

    def test_content_export_redacts_obvious_identifiers(self):
        self.create_job()
        output = StringIO()

        call_command(
            "export_bartender_audit",
            include_content=True,
            context_size=1,
            stdout=output,
        )

        record = json.loads(output.getvalue())
        self.assertEqual(
            record["question"],
            "@Семён проверь <ip>, почта <email>",
        )
        self.assertEqual(record["response"], "Проверю адрес и отвечу @guest.")
        self.assertEqual(
            record["surrounding_context"][0]["text"],
            "Посмотри <url> и напиши @guest",
        )

    def test_private_and_closed_conversations_are_never_exported(self):
        self.create_job(private=True)
        closed_room = Room.objects.create(
            name="Closed",
            slug="closed",
            visibility=Room.Visibility.PRIVATE,
            owner=self.user,
        )
        self.create_job(room=closed_room)
        output = StringIO()

        call_command("export_bartender_audit", include_content=True, stdout=output)

        self.assertEqual(output.getvalue(), "")
