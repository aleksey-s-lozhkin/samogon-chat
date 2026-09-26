"""Session/CSRF and visibility regression tests for the additive mobile API."""
from unittest.mock import AsyncMock, patch

from django.test import Client, TestCase, override_settings
from django.utils import timezone

from chat.models import Message, Room, RoomMembership
from chat.services.messages import MessageService
from users.models import ChatStatus, PersonalBlock, User, UserReport


class MobileApiTests(TestCase):
    def setUp(self):
        self.owner = User.objects.create_user(username="owner")
        self.guest = User.objects.create_user(username="guest")
        self.other = User.objects.create_user(username="other")
        self.public = Room.objects.create(name="Public", slug="general")
        self.client.force_login(self.owner)

    def create_room(self):
        response = self.client.post("/api/v1/chat/rooms/", {"name": "Закрытая", "member_ids": [self.guest.pk]}, content_type="application/json")
        self.assertEqual(response.status_code, 201, response.content)
        return response.json(), "/api/v1/chat/rooms/" + response.json()["slug"] + "/"

    def test_anonymous_endpoints_return_json(self):
        self.client.logout()
        for method, path in [("get", "/api/v1/chat/guests/"), ("get", "/api/v1/users/statuses/"), ("get", "/api/v1/users/me/blocks/"), ("post", "/api/v1/chat/rooms/"), ("get", "/api/v1/chat/rooms/general/"), ("post", f"/api/v1/users/{self.guest.pk}/reports/"), ("put", f"/api/v1/users/me/blocks/{self.guest.pk}/")]:
            with self.subTest(path=path):
                response = getattr(self.client, method)(path)
                self.assertEqual(response.status_code, 401)
                self.assertIn("error", response.json())

    def test_room_lifecycle_and_access_revocation(self):
        data, path = self.create_room()
        self.assertTrue(data["can_manage"])
        self.assertFalse(data["can_leave"])
        self.assertEqual(data["owner"]["id"], self.owner.pk)
        self.assertEqual({u["id"] for u in data["members"]}, {self.owner.pk, self.guest.pk})
        self.assertEqual(self.client.post("/api/v1/chat/rooms/", {"name": "Другая", "member_ids": [self.other.pk]}, content_type="application/json").status_code, 409)
        self.assertEqual(self.client.post(path + "leave/").status_code, 409)
        self.client.force_login(self.other)
        for method in ("get", "patch", "delete"):
            self.assertEqual(getattr(self.client, method)(path).status_code, 404)
        self.client.force_login(self.guest)
        self.assertTrue(self.client.get(path).json()["can_leave"])
        self.assertEqual(self.client.patch(path, {"name": "Нет"}, content_type="application/json").status_code, 403)
        self.assertEqual(self.client.delete(path).status_code, 403)
        self.client.force_login(self.owner)
        response = self.client.patch(path, {"name": "Новое"}, content_type="application/json")
        self.assertEqual(response.json()["name"], "Новое")
        with patch("chat.services.private_rooms.revoke_private_room_access") as revoke, self.captureOnCommitCallbacks(execute=True):
            response = self.client.patch(path, {"member_ids": [self.other.pk]}, content_type="application/json")
            self.assertEqual(response.status_code, 200)
        revoke.assert_called_once_with(room_slug=data["slug"], user_ids={self.guest.pk})
        self.client.force_login(self.guest)
        self.assertEqual(self.client.get(path).status_code, 404)
        self.client.force_login(self.other)
        self.assertEqual(self.client.post(path + "leave/").status_code, 204)
        self.assertEqual(self.client.post(path + "leave/").status_code, 404)
        self.client.force_login(self.owner)
        self.assertEqual(self.client.delete(path).status_code, 204)
        self.assertEqual(self.client.delete(path).status_code, 404)

    def test_invalid_members_and_names(self):
        inactive = User.objects.create_user(username="inactive", is_active=False)
        banned = User.objects.create_user(username="banned", banned_at=timezone.now())
        for ids in ([], [self.guest.pk]*2, [self.guest.pk, self.other.pk, self.owner.pk], [self.owner.pk], [inactive.pk], [banned.pk], [999999]):
            with self.subTest(ids=ids):
                response = self.client.post("/api/v1/chat/rooms/", {"name": "Room", "member_ids": ids}, content_type="application/json")
                self.assertEqual(response.status_code, 400, response.content)
        for name in ("", " "*3, "x"*101):
            self.assertEqual(self.client.post("/api/v1/chat/rooms/", {"name": name, "member_ids": [self.guest.pk]}, content_type="application/json").status_code, 400)
        self.assertFalse(Room.objects.filter(owner=self.owner).exists())

    def test_csrf_required_for_all_mutations(self):
        _, path = self.create_room()
        client = Client(enforce_csrf_checks=True)
        client.force_login(self.owner)
        for method, url in [("post", "/api/v1/chat/rooms/"), ("patch", path), ("delete", path), ("post", path+"leave/"), ("put", f"/api/v1/users/me/blocks/{self.guest.pk}/"), ("delete", f"/api/v1/users/me/blocks/{self.guest.pk}/"), ("post", f"/api/v1/users/{self.guest.pk}/reports/")]:
            response = getattr(client, method)(url)
            self.assertEqual(response.status_code, 403)
            self.assertIn("application/json", response["Content-Type"])
        client.get("/api/v1/push/subscriptions/")
        token = client.cookies["csrftoken"].value
        self.assertEqual(client.put(f"/api/v1/users/me/blocks/{self.guest.pk}/", HTTP_X_CSRFTOKEN=token).status_code, 204)

    @override_settings(BARTENDER_USERNAME="semen")
    def test_guest_snapshot_and_status_catalog(self):
        User.objects.create_user(username="inactive", is_active=False)
        User.objects.create_user(username="semen")
        User.objects.create_user(username="admin", is_superuser=True)
        User.objects.create_user(username="banned", banned_at=timezone.now())
        PersonalBlock.objects.create(owner=self.owner, target=self.other)
        ChatStatus.objects.create(code="testing", label="Тестирую", is_active=True)
        ChatStatus.objects.create(code="retired", label="Скрыт", is_active=False)
        with patch("chat.api.mobile_views.online_users.get_all_users", new=AsyncMock(return_value=["guest", "admin", "other"])):
            result = self.client.get("/api/v1/chat/guests/").json()
        self.assertEqual({g["username"] for g in result["guests"]}, {"owner", "guest"})
        self.assertEqual(result["online"], ["guest"])
        self.assertIn("last_seen_at", result["guests"][0])
        self.assertNotIn("rooms", result)
        statuses = self.client.get("/api/v1/users/statuses/").json()["statuses"]
        self.assertIn({"code": "testing", "label": "Тестирую"}, statuses)
        self.assertNotIn("retired", [s["code"] for s in statuses])
        self.assertEqual(self.client.get("/api/v1/chat/guests/?limit=0").status_code, 400)

    def test_reports_and_idempotent_personal_blocks(self):
        report = f"/api/v1/users/{self.guest.pk}/reports/"
        self.assertEqual(self.client.post(report, {"reason": "invalid"}, content_type="application/json").status_code, 400)
        for expected in (201, 200):
            self.assertEqual(self.client.post(report, {"reason": "abuse", "details": "Причина"}, content_type="application/json").status_code, expected)
        self.assertEqual(UserReport.objects.count(), 1)
        url = f"/api/v1/users/me/blocks/{self.guest.pk}/"
        with patch("users.services.safety.refresh_visibility") as refresh, self.captureOnCommitCallbacks(execute=True):
            for _ in range(2): self.assertEqual(self.client.put(url).status_code, 204)
        refresh.assert_called_once()
        self.assertEqual(PersonalBlock.objects.count(), 1)
        self.assertEqual(self.client.get("/api/v1/users/me/blocks/").json()["blocks"][0]["id"], self.guest.pk)
        self.client.force_login(self.other)
        self.assertEqual(self.client.get("/api/v1/users/me/blocks/").json()["blocks"], [])
        self.assertEqual(self.client.delete(url).status_code, 204)
        self.assertEqual(PersonalBlock.objects.count(), 1)
        self.client.force_login(self.owner)
        for _ in range(2): self.assertEqual(self.client.delete(url).status_code, 204)
        self.assertFalse(PersonalBlock.objects.exists())

    def test_block_filters_history_counts_and_disallows_direct_messages_both_ways(self):
        Message.objects.create(room=self.public, user=self.guest, text="hidden public")
        Message.objects.create(room=self.public, user=self.guest, recipient=self.owner, text="hidden private")
        own = Message.objects.create(room=self.public, user=self.owner, recipient=self.guest, text="own history")
        PersonalBlock.objects.create(owner=self.owner, target=self.guest)
        path = "/api/v1/chat/rooms/general/messages/"
        self.assertEqual([m["message"] for m in self.client.get(path).json()["messages"]], ["own history"])
        self.assertEqual(MessageService.get_unread_count(room=self.public, user_id=self.owner.pk), 0)
        for sender, recipient in ((self.owner, self.guest), (self.guest, self.owner)):
            self.client.force_login(sender)
            response = self.client.post(path, {"message": "no delivery", "recipient": recipient.username}, content_type="application/json")
            self.assertIn(response.status_code, (400, 403), response.content)
        self.assertEqual(Message.objects.count(), 3)
        self.assertTrue(MessageService.can_view_message(message=own, user=self.guest))
        PersonalBlock.objects.all().delete()
        self.client.force_login(self.owner)
        self.assertEqual(len(self.client.get(path).json()["messages"]), 3)

    def test_read_only_production_smoke_accepts_actual_schema_and_responses(self):
        from asgiref.sync import async_to_sync, sync_to_async
        from scripts.production_smoke import check_mobile_api
        self.create_room()
        client = self.client

        class Response:
            def __init__(self, path):
                self.path = path

            async def __aenter__(self):
                self.response = await sync_to_async(client.get)(self.path)
                self.status = self.response.status_code
                return self

            async def __aexit__(self, *args):
                pass

            async def json(self):
                import json
                return json.loads(self.response.content)

        class Session:
            def get(self, url, **kwargs):
                return Response(url.removeprefix("https://testserver"))

        result = async_to_sync(check_mobile_api)(Session(), "https://testserver")
        self.assertEqual(result["private_rooms_checked"], 1)
        self.assertEqual(result["mutations"], "not_run")
        self.assertFalse(UserReport.objects.exists())
        self.assertFalse(PersonalBlock.objects.exists())
