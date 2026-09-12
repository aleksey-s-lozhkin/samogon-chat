import json
from io import StringIO
from types import SimpleNamespace
from unittest.mock import patch

from django.contrib import admin
from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.test import TestCase, override_settings
from django.urls import reverse

from chat.admin import AtmosphereLineAdmin
from chat.models import AtmosphereLine, Room
from chat.selectors import get_published_atmosphere_lines
from chat.services.atmosphere import AtmosphereUnavailable, generate_atmosphere_line
from chat.tasks import generate_atmosphere_line_candidate


User = get_user_model()


class AtmosphereLineTests(TestCase):
    def test_selector_returns_only_approved_active_lines(self):
        approved = AtmosphereLine.objects.create(
            text="Тесты зелёные, можно выдохнуть.",
            kind=AtmosphereLine.Kind.SEMEN,
            status=AtmosphereLine.Status.APPROVED,
        )
        AtmosphereLine.objects.create(
            text="Этот черновик не должен попасть в шапку.",
            kind=AtmosphereLine.Kind.SEMEN,
        )
        AtmosphereLine.objects.create(
            text="Эта строка выключена.",
            kind=AtmosphereLine.Kind.SEMEN,
            status=AtmosphereLine.Status.APPROVED,
            is_active=False,
        )

        lines = get_published_atmosphere_lines()

        self.assertIn(approved.text, lines)
        self.assertNotIn("Этот черновик не должен попасть в шапку.", lines)
        self.assertNotIn("Эта строка выключена.", lines)

    def test_admin_approval_records_moderator_and_time(self):
        moderator = User.objects.create_superuser(username="admin", password="secret")
        line = AtmosphereLine.objects.create(
            text="Логи тихи, но внимательны.",
            kind=AtmosphereLine.Kind.SEMEN,
        )
        model_admin = AtmosphereLineAdmin(AtmosphereLine, admin.site)

        model_admin.approve_lines(
            SimpleNamespace(user=moderator),
            AtmosphereLine.objects.filter(pk=line.pk),
        )

        line.refresh_from_db()
        self.assertEqual(line.status, AtmosphereLine.Status.APPROVED)
        self.assertEqual(line.approved_by, moderator)
        self.assertIsNotNone(line.approved_at)

    def test_chat_embeds_only_published_lines_as_json(self):
        room = Room.objects.create(name="General", slug="atmosphere-room")
        approved = AtmosphereLine.objects.create(
            text="Рефакторинг любит тишину.",
            kind=AtmosphereLine.Kind.SEMEN,
            status=AtmosphereLine.Status.APPROVED,
        )
        draft = AtmosphereLine.objects.create(
            text="Черновик ещё ждёт решения.",
            kind=AtmosphereLine.Kind.SEMEN,
        )

        response = self.client.get(reverse("chat:chat", args=[room.slug]))

        self.assertIn(approved.text, response.context["atmosphere_lines"])
        self.assertNotIn(draft.text, response.context["atmosphere_lines"])
        self.assertContains(response, 'id="atmosphere-lines-data"')


class AtmosphereGenerationTests(TestCase):
    @override_settings(OLLAMA_MODEL="qwen3:8b")
    @patch("chat.services.atmosphere.urlopen")
    def test_generator_uses_no_chat_data_and_validates_reply(self, mock_urlopen):
        mock_urlopen.return_value.__enter__.return_value.read.return_value = (
            '{"message": {"content": "Тесты зелёные, код отдыхает."}}'.encode()
        )

        result = generate_atmosphere_line()

        self.assertEqual(result, "Тесты зелёные, код отдыхает.")
        payload = json.loads(mock_urlopen.call_args.args[0].data)
        serialized = json.dumps(payload, ensure_ascii=False)
        self.assertNotIn("conversation_history", serialized)
        self.assertNotIn("guest_", serialized)

    @patch("chat.services.atmosphere.urlopen")
    def test_generator_rejects_unsafe_or_malformed_reply(self, mock_urlopen):
        mock_urlopen.return_value.__enter__.return_value.read.return_value = (
            '{"message": {"content": "Возьми пиво и забудь про логи."}}'.encode()
        )

        with self.assertRaises(AtmosphereUnavailable):
            generate_atmosphere_line()

    @override_settings(OLLAMA_MODEL="qwen3:8b")
    @patch(
        "chat.tasks.generate_atmosphere_line",
        return_value="Код собран, вечер свободен.",
    )
    def test_background_task_creates_draft(self, generate):
        line_id = generate_atmosphere_line_candidate.run()

        line = AtmosphereLine.objects.get(pk=line_id)
        self.assertEqual(line.status, AtmosphereLine.Status.DRAFT)
        self.assertEqual(line.generated_by_model, "qwen3:8b")

    @patch("chat.management.commands.generate_atmosphere_lines.generate_atmosphere_line_candidate.delay")
    def test_command_queues_requested_number_of_candidates(self, delay):
        output = StringIO()

        call_command("generate_atmosphere_lines", count=3, stdout=output)

        self.assertEqual(delay.call_count, 3)
        self.assertIn("3", output.getvalue())
