import csv
import io
import json

from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from chat.models import BartenderJob, Message, MessageReport, Room
from users.models import User

from .storage import SamogonManifestStaticFilesStorage


class AdminDiagnosticsTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.superuser = User.objects.create_superuser(
            username="admin",
            email="admin@example.invalid",
            password="admin-password",
        )
        cls.staff = User.objects.create_user(
            username="moderator",
            password="moderator-password",
            is_staff=True,
        )
        cls.guest = User.objects.create_user(
            username="guest",
            email="guest@example.invalid",
            password="guest-password",
        )
        cls.public_room, _ = Room.objects.update_or_create(
            slug="u-stoyki",
            defaults={"name": "У стойки"},
        )
        cls.private_room = Room.objects.create(
            name="Закрытая",
            slug="private-room",
            visibility=Room.Visibility.PRIVATE,
            owner=cls.guest,
        )
        cls.public_message = Message.objects.create(
            user=cls.guest,
            room=cls.public_room,
            text="Напишите guest@example.invalid с 192.0.2.10 или @guest",
        )
        cls.direct_message = Message.objects.create(
            user=cls.guest,
            room=cls.public_room,
            recipient=cls.superuser,
            text="Личный текст",
        )
        cls.hidden_message = Message.objects.create(
            user=cls.guest,
            room=cls.public_room,
            text="Скрытый текст",
            hidden_at=timezone.now(),
        )
        cls.private_message = Message.objects.create(
            user=cls.guest,
            room=cls.private_room,
            text="Текст закрытой комнаты",
        )
        MessageReport.objects.create(
            message=cls.public_message,
            reporter=cls.superuser,
            reason=MessageReport.Reason.OTHER,
        )
        BartenderJob.objects.create(
            user=cls.guest,
            room=cls.public_room,
            question=cls.public_message,
            status=BartenderJob.Status.FAILED,
            error_code="model_unavailable",
        )

    def _download(self, **overrides):
        self.client.force_login(self.superuser)
        data = {
            "period": "all",
            "room": "",
            "file_format": "jsonl",
            **overrides,
        }
        response = self.client.post(reverse("admin_export_messages"), data)
        content = b"".join(response.streaming_content).decode()
        return response, content

    def test_diagnostics_requires_staff_login(self):
        response = self.client.get(reverse("admin_diagnostics"))

        self.assertEqual(response.status_code, 302)
        self.assertIn(reverse("admin:login"), response.url)

    def test_admin_index_renders(self):
        self.client.force_login(self.superuser)

        response = self.client.get(reverse("admin:index"), secure=True)

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Самогон")

    def test_admin_login_returns_to_admin(self):
        response = self.client.post(
            reverse("admin:login"),
            {
                "username": self.superuser.username,
                "password": "admin-password",
                "next": reverse("admin:index"),
            },
        )

        self.assertRedirects(response, reverse("admin:index"), fetch_redirect_response=False)

    def test_diagnostics_shows_activity_and_persistent_failures(self):
        self.client.force_login(self.staff)

        response = self.client.get(reverse("admin_diagnostics"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Статистика и диагностика")
        self.assertContains(response, "Активность за 14 дней")
        self.assertContains(response, "model_unavailable")
        self.assertContains(response, "Новых жалоб")
        self.assertNotContains(response, "Выгрузить сообщения")

    def test_export_is_restricted_to_superusers(self):
        self.client.force_login(self.staff)

        response = self.client.get(reverse("admin_export_messages"))

        self.assertEqual(response.status_code, 403)

    def test_default_export_contains_only_public_visible_metadata(self):
        response, content = self._download()

        self.assertEqual(response.status_code, 200)
        self.assertIn("attachment;", response["Content-Disposition"])
        records = [json.loads(line) for line in content.splitlines()]
        self.assertEqual(records[0]["record_type"], "metadata")
        self.assertEqual(records[0]["message_count"], 1)
        self.assertEqual(len(records), 2)
        self.assertEqual(records[1]["id"], self.public_message.id)
        self.assertEqual(records[1]["author"], "user_001")
        self.assertNotIn("text", records[1])
        self.assertNotIn(self.guest.username, content)
        self.assertNotIn(self.guest.email, content)

    def test_content_export_redacts_obvious_identifiers(self):
        response, content = self._download(
            include_content="on",
            redact_content="on",
        )

        self.assertEqual(response.status_code, 200)
        message = json.loads(content.splitlines()[1])
        self.assertEqual(
            message["text"],
            "Напишите <email> с <ip> или @guest",
        )

    def test_private_hidden_and_raw_content_require_explicit_options(self):
        response, content = self._download(
            include_content="on",
            include_private="on",
            include_hidden="on",
        )

        self.assertEqual(response.status_code, 200)
        records = [json.loads(line) for line in content.splitlines()[1:]]
        self.assertEqual({record["id"] for record in records}, {
            self.public_message.id,
            self.direct_message.id,
            self.hidden_message.id,
            self.private_message.id,
        })
        self.assertIn("Личный текст", {record["text"] for record in records})

    def test_csv_export_neutralizes_spreadsheet_formulas(self):
        self.public_message.text = "=IMPORTXML(\"https://example.invalid\")"
        self.public_message.save(update_fields=("text",))

        response, content = self._download(
            file_format="csv",
            include_content="on",
        )

        self.assertEqual(response.status_code, 200)
        row = next(csv.DictReader(io.StringIO(content)))
        self.assertTrue(row["text"].startswith("'="))

    def test_message_changelist_links_to_export_for_superuser(self):
        self.client.force_login(self.superuser)

        response = self.client.get(reverse("admin:chat_message_changelist"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, reverse("admin_export_messages"))


class AdminStaticStorageTests(TestCase):
    def test_jazzmin_theme_directory_does_not_require_manifest_entry(self):
        storage = SamogonManifestStaticFilesStorage(
            location="/tmp/samogon-static-storage-test",
            base_url="/static/",
        )

        self.assertEqual(
            storage.url("vendor/bootswatch"),
            "/static/vendor/bootswatch",
        )
