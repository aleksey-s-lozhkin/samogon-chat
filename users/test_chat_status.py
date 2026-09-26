from importlib import import_module
from types import SimpleNamespace

from django.apps import apps
from django.contrib.auth.models import Group, Permission
from django.test import TestCase
from django.urls import reverse

from users.admin import ChatUserChangeForm
from users.forms import ProfileForm
from users.models import ChatStatus, User


class ChatStatusTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="status-user")
        self.status = ChatStatus.objects.create(code="coding", label="Пишу код", position=20)

    def test_seed_preserves_existing_user_status_and_edited_labels(self):
        self.user.presence_status = "thinking"
        self.user.save()
        ChatStatus.objects.filter(code="thinking").update(label="Размышляю")
        migration = import_module("users.migrations.0010_chatstatus_alter_user_presence_status")
        migration.seed_statuses(apps, SimpleNamespace(connection=SimpleNamespace(alias="default")))
        self.user.refresh_from_db()
        self.assertEqual(self.user.presence_status, "thinking")
        self.assertEqual(self.user.get_presence_status_display(), "Размышляю")
        self.assertEqual(ChatStatus.objects.filter(code="thinking").count(), 1)

    def test_catalog_order_disable_and_retained_selection(self):
        first = ChatStatus.objects.create(code="first", label="Первый", position=0)
        self.assertLess(
            [code for code, _ in ChatStatus.choices_for()].index(first.code),
            [code for code, _ in ChatStatus.choices_for()].index(self.status.code),
        )
        self.status.is_active = False
        self.status.save()
        self.assertNotIn(("coding", "Пишу код"), ChatStatus.choices_for())
        self.user.presence_status = "coding"
        for form_type in (ProfileForm, ChatUserChangeForm):
            form = form_type(instance=self.user)
            self.assertIn(("coding", "Пишу код"), form.fields["presence_status"].choices)
        self.assertEqual(self.user.get_presence_status_display(), "Пишу код")

    def test_profile_validates_against_catalog(self):
        data = {"username": self.user.username, "email": "status@example.invalid", "message_color": "amber"}
        for value, valid in (("coding", True), ("unknown", False), ("", True)):
            form = ProfileForm({**data, "presence_status": value}, instance=self.user)
            self.assertEqual(form.is_valid(), valid, form.errors)
        self.status.is_active = False
        self.status.save()
        self.user.presence_status = ""
        form = ProfileForm({**data, "presence_status": "coding"}, instance=self.user)
        self.assertFalse(form.is_valid())

    def test_api_accepts_custom_status_and_rejects_disabled(self):
        self.client.force_login(self.user)
        url = "/api/v1/users/me/"
        response = self.client.patch(url, {"presence_status": "coding"}, content_type="application/json")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["presence_status_label"], "Пишу код")
        self.status.is_active = False
        self.status.save()
        response = self.client.patch(url, {"presence_status": "coding"}, content_type="application/json")
        self.assertEqual(response.status_code, 400)
        response = self.client.patch(url, {"presence_status": ""}, content_type="application/json")
        self.assertEqual(response.status_code, 200)

    def test_admin_edits_label_and_user_status_but_preserves_code(self):
        admin = User.objects.create_superuser(username="status-admin", email="admin@example.invalid", password="test")
        self.client.force_login(admin)
        url = reverse("admin:users_chatstatus_change", args=[self.status.pk])
        response = self.client.post(url, {"code": "changed", "label": "Пишу тесты", "position": 3, "is_active": "on", "_save": "Сохранить"})
        self.assertEqual(response.status_code, 302)
        self.status.refresh_from_db()
        self.assertEqual(self.status.code, "coding")
        self.assertEqual(self.status.label, "Пишу тесты")
        form = ChatUserChangeForm({"username": self.user.username, "presence_status": "coding", "is_active": "on", "date_joined": self.user.date_joined, "message_color": "amber"}, instance=self.user)
        self.assertTrue(form.is_valid(), form.errors)
        form.save()
        self.user.refresh_from_db()
        self.assertEqual(self.user.presence_status, "coding")
        self.assertEqual(self.client.get(reverse("admin:users_chatstatus_delete", args=[self.status.pk])).status_code, 403)
        response = self.client.get(reverse("admin:users_user_change", args=[self.user.pk]))
        self.assertContains(response, 'name="presence_status"')

    def test_moderator_cannot_edit_catalog_or_profile_status(self):
        self.user.is_staff = True
        self.user.presence_status = "coding"
        self.user.save()
        group, _ = Group.objects.get_or_create(name="Moderators")
        self.user.groups.add(group)
        self.user.user_permissions.add(Permission.objects.get(codename="change_user"))
        self.client.force_login(self.user)
        self.assertEqual(self.client.get(reverse("admin:users_chatstatus_changelist")).status_code, 403)
        response = self.client.get(reverse("admin:users_user_change", args=[self.user.pk]))
        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, 'name="presence_status"')
        self.assertContains(response, "Пишу код")
