import json
import tempfile
from unittest.mock import AsyncMock, patch

from asgiref.sync import async_to_sync, sync_to_async
from channels.layers import get_channel_layer
from channels.routing import URLRouter
from channels.testing import WebsocketCommunicator
from django.contrib.admin.sites import AdminSite
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import RequestFactory, TestCase, TransactionTestCase, override_settings

from chat.models import Message, MessageReaction, Room, RoomMembership
from chat.routing import websocket_urlpatterns
from chat.services.attachments import create_attachments
from chat.services.messages import MessageService
from chat.services.private_rooms import update_private_room
from users.admin import UserReportAdmin
from users.models import PersonalBlock, User, UserReport
from users.services.push import send_direct_message_push
from users.services.safety import set_personal_block


class SafetyVisibilityTests(TestCase):
    def setUp(self):
        self.viewer = User.objects.create_user(username="viewer")
        self.target = User.objects.create_user(username="target")
        self.other = User.objects.create_user(username="other")
        self.room = Room.objects.create(name="Public", slug="general")
        self.client.force_login(self.viewer)

    def test_reply_reactions_and_search_hide_blocked_author(self):
        source = Message.objects.create(room=self.room, user=self.target, text="hidden-phrase")
        reply = Message.objects.create(room=self.room, user=self.other, text="reply", reply_to=source)
        MessageReaction.objects.create(message=reply, user=self.target, emoji="👍")
        PersonalBlock.objects.create(owner=self.viewer, target=self.target)
        result = MessageService.serialize_message(reply, self.viewer.pk)
        self.assertEqual(result["reply_to"], {"id": source.pk, "available": False})
        self.assertEqual(result["reactions"], [])
        response = self.client.post(f"/api/v1/chat/rooms/general/messages/{reply.pk}/reactions/", {"emoji": "👍"}, content_type="application/json")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["users"], ["viewer"])
        self.assertEqual(response.json()["count"], 1)
        response = self.client.get("/chat/search/", {"q": "hidden-phrase"})
        self.assertEqual(response.context["results"], [])
        self.assertFalse(MessageService.can_view_message(message=source, user=self.viewer))

    def test_attachment_download_and_new_upload_are_blocked(self):
        with tempfile.TemporaryDirectory() as media, override_settings(MEDIA_ROOT=media):
            incoming = Message.objects.create(room=self.room, user=self.target, recipient=self.viewer, text="incoming")
            outgoing = Message.objects.create(room=self.room, user=self.viewer, recipient=self.target, text="outgoing")
            attachments = create_attachments(message=incoming, uploaded_files=[SimpleUploadedFile("sample.txt", b"test")])
            url = MessageService.serialize_attachments(incoming)[0]["download_url"]
            response = self.client.get(url)
            self.assertEqual(response.status_code, 200)
            response.close()
            PersonalBlock.objects.create(owner=self.viewer, target=self.target)
            self.assertEqual(self.client.get(url).status_code, 404)
            response = self.client.post(f"/api/v1/chat/rooms/general/messages/{outgoing.pk}/attachments/", {"files": SimpleUploadedFile("sample.txt", b"blocked")})
            self.assertEqual(response.status_code, 400)
            self.assertFalse(outgoing.attachments.exists())

    def test_push_is_suppressed_for_blocked_pair(self):
        PersonalBlock.objects.create(owner=self.viewer, target=self.target)
        with patch("users.services.push.webpush") as push:
            self.assertEqual(send_direct_message_push(recipient_id=self.viewer.pk, sender_id=self.target.pk, room_slug=self.room.slug), 0)
        push.assert_not_called()

    def test_report_resolution_preserves_first_moderator_decision(self):
        report = UserReport.objects.create(reporter=self.viewer, target=self.target, reason="abuse")
        stale = UserReport.objects.get(pk=report.pk)
        admin = UserReportAdmin(UserReport, AdminSite())
        request = RequestFactory().post("/admin/")
        request.user = self.other
        report.resolution_note = "Рассмотрено"
        admin.save_model(request, report, None, True)
        stale.resolution_note = "Перезаписано"
        admin.save_model(request, stale, None, True)
        report.refresh_from_db()
        self.assertEqual(report.resolution_note, "Рассмотрено")
        self.assertEqual(report.resolved_by, self.other)
        self.assertFalse(admin.has_add_permission(request))
        self.assertFalse(admin.has_delete_permission(request, report))


class MobileSocketTests(TransactionTestCase):
    def setUp(self):
        self.viewer = User.objects.create_user(username="viewer")
        self.target = User.objects.create_user(username="target")
        self.other = User.objects.create_user(username="other")
        self.room = Room.objects.create(name="Public", slug="general")
        self.application = URLRouter(websocket_urlpatterns)

    async def connect(self, user, slug="general"):
        socket = WebsocketCommunicator(self.application, f"/ws/chat/{slug}/")
        socket.scope["user"] = user
        connected, _ = await socket.connect()
        self.assertTrue(connected)
        await socket.receive_json_from()  # history
        return socket

    async def event(self, socket, kind):
        for _ in range(15):
            event = await socket.receive_json_from(timeout=2)
            if event["type"] == kind:
                return event
        self.fail(f"Missing {kind}")

    def test_block_refreshes_open_history_and_suppresses_queued_delivery(self):
        hidden = Message.objects.create(room=self.room, user=self.target, text="hidden")
        async_to_sync(self.block_flow)(hidden.pk)

    async def block_flow(self, hidden_id):
        socket = await self.connect(self.viewer)
        try:
            await self.event(socket, "user_presence")
            await sync_to_async(set_personal_block)(owner=self.viewer, target_id=self.target.pk, blocked=True)
            history = await self.event(socket, "history")
            self.assertEqual(history["messages"], [])
            snapshot = await self.event(socket, "unread_snapshot")
            self.assertEqual(snapshot["rooms"]["general"], {"general": False, "personal": False})
            presence = await self.event(socket, "user_presence")
            self.assertNotIn("target", [u["username"] for u in presence["users"]])
            await get_channel_layer().group_send("chat_general", {"type": "send_message", "id": hidden_id, "room_slug": "general", "room_private": False})
            self.assertTrue(await socket.receive_nothing(timeout=0.15))
            await socket.send_json_to({"message": "blocked dm", "recipient": "target"})
            error = await self.event(socket, "error")
            self.assertTrue(error)
            await sync_to_async(set_personal_block)(owner=self.viewer, target_id=self.target.pk, blocked=False)
            history = await self.event(socket, "history")
            self.assertEqual([m["id"] for m in history["messages"]], [hidden_id])
        finally:
            await socket.disconnect()

    def test_membership_update_closes_existing_socket(self):
        room = Room.objects.create(name="Private", slug="private", visibility=Room.Visibility.PRIVATE, owner=self.viewer)
        room.members.set([self.viewer, self.target])
        async_to_sync(self.revoke_flow)(room.pk)

    async def revoke_flow(self, room_id):
        socket = await self.connect(self.target, "private")
        try:
            await self.event(socket, "user_presence")
            await sync_to_async(update_private_room)(actor=self.viewer, room_id=room_id, data={"members": [self.other.pk]})
            output = await socket.receive_output(timeout=2)
            self.assertEqual(output, {"type": "websocket.close", "code": 4403})
        finally:
            await socket.disconnect()
