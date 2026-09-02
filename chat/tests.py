import json
import tempfile
from io import BytesIO
from io import StringIO
from unittest.mock import patch

from asgiref.sync import async_to_sync
from channels.routing import URLRouter
from channels.testing import WebsocketCommunicator
from PIL import Image
from django.contrib.auth import get_user_model
from django.contrib.auth.models import AnonymousUser, Permission
from django.conf import settings
from django.core.files.uploadedfile import SimpleUploadedFile
from django.core.management import call_command
from django.test import TestCase, TransactionTestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from .models import (
    Attachment,
    Message,
    MessageReaction,
    Note,
    NoteAttachment,
    Room,
    RoomMembership,
    RoomReadState,
)
from .routing import websocket_urlpatterns
from .services.attachments import (
    AttachmentValidationError,
    create_attachment,
    create_attachments,
    validate_attachment,
)
from .services.messages import MessageService
from .services.bartender import BARTENDER_LANGUAGE_FALLBACK, bartender
from .validators import validate_message


User = get_user_model()


class AttachmentServiceTests(TestCase):
    def setUp(self):
        self.media_directory = tempfile.TemporaryDirectory()
        self.settings_override = override_settings(
            MEDIA_ROOT=self.media_directory.name,
        )
        self.settings_override.enable()
        self.user = User.objects.create_user(username="alex")
        self.room = Room.objects.create(name="General", slug="general")
        self.message = Message.objects.create(
            user=self.user,
            room=self.room,
            text="Файл для проверки",
        )

    def tearDown(self):
        self.settings_override.disable()
        self.media_directory.cleanup()

    @staticmethod
    def make_png_file(name="picture.png"):
        image_data = BytesIO()
        Image.new("RGB", (2, 2), color="#c6753a").save(image_data, format="PNG")
        return SimpleUploadedFile(
            name,
            image_data.getvalue(),
            content_type="image/png",
        )

    def test_create_attachment_saves_verified_image_under_random_name(self):
        attachment = create_attachment(
            message=self.message,
            uploaded_file=self.make_png_file(),
        )

        self.assertEqual(attachment.kind, Attachment.Kind.IMAGE)
        self.assertEqual(attachment.content_type, "image/png")
        self.assertEqual(attachment.original_name, "picture.png")
        self.assertTrue(attachment.file.name.startswith("chat/attachments/"))
        self.assertNotIn("picture", attachment.file.name)

    def test_history_serializes_existing_attachments_after_reload(self):
        attachment = create_attachment(
            message=self.message,
            uploaded_file=SimpleUploadedFile("notes.txt", b"content"),
        )

        history = MessageService.get_room_messages(
            self.room,
            viewer_id=self.user.id,
        )

        self.assertEqual(history[0]["attachments"][0]["id"], str(attachment.id))
        self.assertEqual(history[0]["attachments"][0]["name"], "notes.txt")
        self.assertIn("/chat/attachments/", history[0]["attachments"][0]["preview_url"])

    def test_rejects_fake_image_even_with_image_extension(self):
        uploaded_file = SimpleUploadedFile(
            "not-an-image.png",
            b"not an image",
            content_type="image/png",
        )

        with self.assertRaisesMessage(
            AttachmentValidationError,
            "Файл не является корректным изображением.",
        ):
            validate_attachment(uploaded_file)

    def test_accepts_utf8_text_and_rejects_binary_content(self):
        text_file = SimpleUploadedFile("notes.txt", "Привет".encode())
        binary_file = SimpleUploadedFile("notes.txt", b"\x00\x01")

        self.assertEqual(
            validate_attachment(text_file).kind,
            Attachment.Kind.FILE,
        )
        with self.assertRaises(AttachmentValidationError):
            validate_attachment(binary_file)

    def test_rejects_unsupported_extension(self):
        uploaded_file = SimpleUploadedFile("archive.zip", b"PK\x03\x04")

        with self.assertRaisesMessage(
            AttachmentValidationError,
            "Этот тип файла пока не поддерживается.",
        ):
            validate_attachment(uploaded_file)

    def test_rejects_more_than_three_attachments_per_message(self):
        for number in range(3):
            create_attachment(
                message=self.message,
                uploaded_file=SimpleUploadedFile(
                    f"note-{number}.txt",
                    b"content",
                ),
            )

        with self.assertRaisesMessage(
            AttachmentValidationError,
            "К сообщению можно добавить не больше трёх файлов.",
        ):
            create_attachment(
                message=self.message,
                uploaded_file=SimpleUploadedFile("fourth.txt", b"content"),
            )

    def test_rejects_whole_batch_before_writing_any_file(self):
        with self.assertRaises(AttachmentValidationError):
            create_attachments(
                message=self.message,
                uploaded_files=[
                    SimpleUploadedFile("valid.txt", b"content"),
                    SimpleUploadedFile("blocked.zip", b"PK\x03\x04"),
                ],
            )

        self.assertFalse(self.message.attachments.exists())


class AttachmentDownloadTests(TestCase):
    def setUp(self):
        self.media_directory = tempfile.TemporaryDirectory()
        self.settings_override = override_settings(
            MEDIA_ROOT=self.media_directory.name,
        )
        self.settings_override.enable()
        self.author = User.objects.create_user(username="author")
        self.recipient = User.objects.create_user(username="recipient")
        self.outsider = User.objects.create_user(username="outsider")
        self.room = Room.objects.create(name="General", slug="general")
        self.message = Message.objects.create(
            user=self.author,
            room=self.room,
            recipient=self.recipient,
            text="Личный файл",
        )
        self.attachment = create_attachment(
            message=self.message,
            uploaded_file=SimpleUploadedFile("note.txt", b"secret"),
        )
        self.url = reverse(
            "chat:download_attachment",
            args=[self.attachment.id],
        )

    def tearDown(self):
        self.settings_override.disable()
        self.media_directory.cleanup()

    @override_settings(DEBUG=True)
    def test_recipient_can_download_personal_attachment(self):
        self.client.force_login(self.recipient)

        response = self.client.get(self.url)

        self.assertEqual(response.status_code, 200)
        self.assertEqual(b"".join(response.streaming_content), b"secret")
        self.assertIn("attachment", response["Content-Disposition"])

    @override_settings(DEBUG=False)
    def test_production_response_uses_internal_nginx_redirect(self):
        self.client.force_login(self.recipient)

        response = self.client.get(self.url)

        self.assertEqual(response.status_code, 200)
        self.assertTrue(
            response["X-Accel-Redirect"].startswith("/media/chat/attachments/")
        )

    def test_outsider_cannot_download_personal_attachment(self):
        self.client.force_login(self.outsider)

        response = self.client.get(self.url)

        self.assertEqual(response.status_code, 404)

    def test_hidden_message_makes_attachment_unavailable(self):
        self.message.hidden_at = timezone.now()
        self.message.save(update_fields=("hidden_at",))
        self.client.force_login(self.author)

        response = self.client.get(self.url)

        self.assertEqual(response.status_code, 404)

    def test_private_room_attachment_requires_membership(self):
        private_room = Room.objects.create(
            name="Тайный столик",
            slug="taynyy-stolik",
            visibility=Room.Visibility.PRIVATE,
            owner=self.author,
        )
        RoomMembership.objects.create(room=private_room, user=self.author)
        private_message = Message.objects.create(
            user=self.author,
            room=private_room,
            text="Только для своих",
        )
        private_attachment = create_attachment(
            message=private_message,
            uploaded_file=SimpleUploadedFile("private.txt", b"private"),
        )
        self.client.force_login(self.outsider)

        response = self.client.get(
            reverse("chat:download_attachment", args=[private_attachment.id])
        )

        self.assertEqual(response.status_code, 404)


class AttachmentUploadViewTests(TestCase):
    def setUp(self):
        self.media_directory = tempfile.TemporaryDirectory()
        self.settings_override = override_settings(
            MEDIA_ROOT=self.media_directory.name,
        )
        self.settings_override.enable()
        self.author = User.objects.create_user(username="author")
        self.outsider = User.objects.create_user(username="outsider")
        self.room = Room.objects.create(name="General", slug="general")
        self.message = Message.objects.create(
            user=self.author,
            room=self.room,
            text="Сообщение с файлами",
        )
        self.url = reverse("chat:message_attachments", args=[self.message.id])

    def tearDown(self):
        self.settings_override.disable()
        self.media_directory.cleanup()

    def test_chat_page_sets_csrf_cookie_for_attachment_upload(self):
        self.client.force_login(self.author)

        response = self.client.get(reverse("chat:chat", args=[self.room.slug]))

        self.assertIn(settings.CSRF_COOKIE_NAME, response.cookies)

    @patch("chat.views.broadcast_attachment_update")
    def test_author_can_upload_files_and_receives_safe_urls(self, broadcast):
        self.client.force_login(self.author)

        response = self.client.post(
            self.url,
            {
                "files": [
                    SimpleUploadedFile("notes.txt", b"content"),
                    SimpleUploadedFile("guide.pdf", b"%PDF-1.7\ncontent"),
                ],
            },
        )

        self.assertEqual(response.status_code, 201)
        self.assertEqual(self.message.attachments.count(), 2)
        attachment_data = response.json()["attachments"]
        self.assertEqual(attachment_data[0]["name"], "notes.txt")
        self.assertIn("/chat/attachments/", attachment_data[0]["preview_url"])
        self.assertNotIn("chat/attachments/", attachment_data[0]["name"])
        broadcast.assert_called_once()

    def test_non_author_cannot_add_files_to_someone_elses_message(self):
        self.client.force_login(self.outsider)

        response = self.client.post(
            self.url,
            {"files": [SimpleUploadedFile("notes.txt", b"content")]},
        )

        self.assertEqual(response.status_code, 404)


class MessageDeleteViewTests(TestCase):
    def setUp(self):
        self.author = User.objects.create_user(username="author")
        self.outsider = User.objects.create_user(username="outsider")
        self.moderator = User.objects.create_user(username="moderator")
        self.moderator.user_permissions.add(
            Permission.objects.get(
                content_type__app_label="chat",
                codename="moderate_message",
            )
        )
        self.room = Room.objects.create(name="General", slug="general")
        self.message = Message.objects.create(
            user=self.author,
            room=self.room,
            text="Реплика для удаления",
        )
        self.url = reverse("chat:delete_message", args=[self.message.id])

    @patch("chat.views.broadcast_message_deleted")
    def test_author_can_hide_own_message(self, broadcast):
        self.client.force_login(self.author)

        response = self.client.post(self.url)

        self.assertEqual(response.status_code, 200)
        self.message.refresh_from_db()
        self.assertIsNotNone(self.message.hidden_at)
        self.assertEqual(self.message.hidden_by, self.author)
        self.assertEqual(self.message.hidden_reason, "Удалено автором.")
        broadcast.assert_called_once_with(self.message)

    def test_guest_cannot_hide_someone_elses_message(self):
        self.client.force_login(self.outsider)

        response = self.client.post(self.url)

        self.assertEqual(response.status_code, 403)
        self.message.refresh_from_db()
        self.assertIsNone(self.message.hidden_at)

    @patch("chat.views.broadcast_message_deleted")
    def test_moderator_can_hide_any_message_and_gets_audit_event(self, broadcast):
        self.client.force_login(self.moderator)

        response = self.client.post(self.url)

        self.assertEqual(response.status_code, 200)
        self.message.refresh_from_db()
        self.assertEqual(self.message.hidden_by, self.moderator)
        self.assertTrue(
            self.message.moderation_events.filter(
                moderator=self.moderator,
                action="hide_message",
            ).exists()
        )
        broadcast.assert_called_once_with(self.message)


class NotesViewTests(TestCase):
    def setUp(self):
        self.media_directory = tempfile.TemporaryDirectory()
        self.settings_override = override_settings(
            MEDIA_ROOT=self.media_directory.name,
        )
        self.settings_override.enable()
        self.author = User.objects.create_user(username="author")
        self.reader = User.objects.create_user(username="reader")
        self.outsider = User.objects.create_user(username="outsider")
        self.room = Room.objects.create(name="General", slug="general")
        self.message = Message.objects.create(
            user=self.author,
            room=self.room,
            text="Сохранённая реплика",
        )
        self.create_url = reverse("chat:create_note")

    def tearDown(self):
        self.settings_override.disable()
        self.media_directory.cleanup()

    def test_user_can_save_visible_message_once(self):
        self.client.force_login(self.reader)

        response = self.client.post(
            self.create_url,
            data=json.dumps({"source_message_id": self.message.id}),
            content_type="application/json",
        )
        repeated_response = self.client.post(
            self.create_url,
            data=json.dumps({"source_message_id": self.message.id}),
            content_type="application/json",
        )

        self.assertEqual(response.status_code, 201)
        self.assertEqual(repeated_response.status_code, 200)
        self.assertEqual(self.reader.chat_notes.count(), 1)
        note = self.reader.chat_notes.get()
        self.assertEqual(note.text, self.message.text)
        self.assertEqual(note.source_author, self.author.username)

    def test_user_can_create_and_delete_private_note(self):
        self.client.force_login(self.reader)

        response = self.client.post(
            self.create_url,
            data=json.dumps({"text": "Не забыть проверить логи."}),
            content_type="application/json",
        )
        note = self.reader.chat_notes.get()
        delete_response = self.client.post(
            reverse("chat:delete_note", args=[note.id])
        )

        self.assertEqual(response.status_code, 201)
        self.assertRedirects(delete_response, reverse("chat:notes"))
        self.assertFalse(self.reader.chat_notes.exists())

    def test_uninvited_guest_cannot_save_private_room_message(self):
        private_room = Room.objects.create(
            name="Тайный столик",
            slug="taynyy-stolik",
            visibility=Room.Visibility.PRIVATE,
            owner=self.author,
        )
        RoomMembership.objects.create(room=private_room, user=self.author)
        private_message = Message.objects.create(
            user=self.author,
            room=private_room,
            text="Только для своих",
        )
        self.client.force_login(self.outsider)

        response = self.client.post(
            self.create_url,
            data=json.dumps({"source_message_id": private_message.id}),
            content_type="application/json",
        )

        self.assertEqual(response.status_code, 404)
        self.assertFalse(self.outsider.chat_notes.exists())

    def test_saved_note_keeps_its_own_attachment_after_source_is_hidden(self):
        attachment = create_attachment(
            message=self.message,
            uploaded_file=SimpleUploadedFile("plan.txt", b"ship it"),
        )
        self.client.force_login(self.reader)

        response = self.client.post(
            self.create_url,
            data=json.dumps({"source_message_id": self.message.id}),
            content_type="application/json",
        )
        note_attachment = NoteAttachment.objects.get(note__user=self.reader)
        MessageService.hide_message(message=self.message, actor=self.author)

        self.assertEqual(response.status_code, 201)
        self.assertNotEqual(note_attachment.file.name, attachment.file.name)
        download = self.client.get(
            reverse("chat:download_note_attachment", args=[note_attachment.id])
        )
        self.assertEqual(download.status_code, 200)
        self.assertEqual(
            download["X-Accel-Redirect"],
            f"/media/{note_attachment.file.name}",
        )
        with note_attachment.file.open("rb") as copied_file:
            self.assertEqual(copied_file.read(), b"ship it")

    def test_notes_and_profile_return_to_last_open_room(self):
        self.client.force_login(self.reader)
        self.client.get(reverse("chat:chat", args=[self.room.slug]))

        notes_response = self.client.get(reverse("chat:notes"))
        profile_response = self.client.get(reverse("profile"))

        room_url = reverse("chat:chat", args=[self.room.slug])
        self.assertContains(notes_response, f'href="{room_url}"')
        self.assertContains(profile_response, f'href="{room_url}"')

    def test_backfill_command_copies_attachments_to_old_note(self):
        create_attachment(
            message=self.message,
            uploaded_file=SimpleUploadedFile("old-plan.txt", b"old file"),
        )
        note = Note.objects.create(
            user=self.reader,
            source_message=self.message,
            source_author=self.author.username,
            text=self.message.text,
        )

        output = StringIO()
        call_command("backfill_note_attachments", stdout=output)

        self.assertEqual(note.attachments.count(), 1)
        self.assertIn("дополнено заметок — 1", output.getvalue())


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

    def test_reaction_summary_keeps_count_and_current_user_state(self):
        second_user = User.objects.create_user(username="maria")
        message = MessageService.create_message(
            user_id=self.user.id,
            room=self.room,
            text="Новый деплой",
        )
        MessageReaction.objects.create(
            message=message,
            user=self.user,
            emoji=MessageReaction.Emoji.FIRE,
        )
        MessageReaction.objects.create(
            message=message,
            user=second_user,
            emoji=MessageReaction.Emoji.FIRE,
        )

        history = MessageService.get_room_messages(
            self.room,
            viewer_id=self.user.id,
        )

        self.assertEqual(
            history[0]["reactions"],
            [{
                "emoji": "🔥",
                "count": 2,
                "reacted": True,
                "users": ["alex", "maria"],
            }],
        )

    def test_reaction_can_be_toggled_only_by_message_viewer(self):
        outsider = User.objects.create_user(username="outsider")
        message = MessageService.create_message(
            user_id=self.user.id,
            room=self.room,
            text="Личная реплика",
            recipient_id=User.objects.create_user(username="reader").id,
        )

        with self.assertRaises(PermissionError):
            MessageService.toggle_reaction(
                message=message,
                user=outsider,
                emoji=MessageReaction.Emoji.LIKE,
            )

        count, active, users = MessageService.toggle_reaction(
            message=message,
            user=self.user,
            emoji=MessageReaction.Emoji.LIKE,
        )
        self.assertEqual((count, active, users), (1, True, ["alex"]))

        count, active, users = MessageService.toggle_reaction(
            message=message,
            user=self.user,
            emoji=MessageReaction.Emoji.LIKE,
        )
        self.assertEqual((count, active, users), (0, False, []))

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

    def test_chat_has_separate_people_panel_and_sidebar_bartender_action(self):
        self.client.force_login(self.user)

        response = self.client.get(reverse("chat:chat", args=[self.room.slug]))

        self.assertContains(response, 'class="rooms-sidebar"')
        self.assertContains(response, 'class="presence-sidebar"')
        self.assertContains(response, 'data-chat-action="bartender"')
        self.assertNotContains(response, 'id="bartender-trigger"')

    def test_robots_disallows_indexing_closed_beta(self):
        response = self.client.get(reverse("robots"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Disallow: /")

    def test_pwa_endpoints_are_available(self):
        home = self.client.get(reverse("home"))
        manifest = self.client.get(reverse("pwa_manifest"))
        service_worker = self.client.get(reverse("service_worker"))
        offline = self.client.get(reverse("offline"))

        self.assertContains(home, 'rel="manifest"')
        self.assertEqual(manifest.status_code, 200)
        self.assertEqual(manifest["Content-Type"], "application/manifest+json")
        self.assertEqual(manifest.json()["display"], "standalone")
        self.assertEqual(service_worker.status_code, 200)
        self.assertIn("application/javascript", service_worker["Content-Type"])
        self.assertEqual(service_worker["Service-Worker-Allowed"], "/")
        self.assertContains(service_worker, 'url.pathname.startsWith("/static/")')
        self.assertContains(service_worker, 'fetch(request).then((response)')
        self.assertNotContains(service_worker, 'url.pathname.startsWith("/chat/")')
        self.assertEqual(offline.status_code, 200)

    def test_chat_script_renders_time_before_attachments(self):
        with open(settings.BASE_DIR / "static/chat/js/chat.js", encoding="utf-8") as script:
            source = script.read()

        self.assertIn("content.append(author, text, time);", source)
        self.assertLess(
            source.index("content.append(author, text, time);"),
            source.index("renderMessageAttachments(content, data.attachments || []);"),
        )

    def test_chat_uses_a_custom_delete_dialog(self):
        with open(settings.BASE_DIR / "static/chat/js/chat.js", encoding="utf-8") as script:
            source = script.read()

        self.assertIn("delete-message-modal", source)
        self.assertNotIn("window.confirm", source)


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
            history_message.id,
            history_message.created_at.isoformat(),
        )
        self.assertTrue(
            Message.objects.filter(room=self.room, user=self.user, text="New message").exists(),
        )

    def test_user_can_toggle_reaction_over_websocket(self):
        message = Message.objects.create(
            room=self.room,
            user=self.user,
            text="Реакция на месте",
        )

        async_to_sync(self._assert_reaction_toggle)(message.id)
        self.assertFalse(MessageReaction.objects.filter(message=message).exists())

    async def _assert_reaction_toggle(self, message_id):
        communicator = WebsocketCommunicator(self.application, "/ws/chat/general/")
        communicator.scope["user"] = self.user

        connected, _ = await communicator.connect()
        self.assertTrue(connected)
        await communicator.receive_json_from()
        await communicator.receive_json_from()
        await communicator.receive_json_from()

        await communicator.send_json_to(
            {"type": "reaction", "message_id": message_id, "emoji": "🔥"}
        )
        added = await communicator.receive_json_from()
        self.assertEqual(added["type"], "reaction_update")
        self.assertEqual(added["count"], 1)
        self.assertTrue(added["active"])
        self.assertEqual(added["users"], ["alex"])

        await communicator.send_json_to(
            {"type": "reaction", "message_id": message_id, "emoji": "🔥"}
        )
        removed = await communicator.receive_json_from()
        self.assertEqual(removed["count"], 0)
        self.assertFalse(removed["active"])

        await communicator.disconnect()

    async def _assert_history_validation_and_delivery(self, message_id, created_at):
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
                        "id": message_id,
                        "username": "alex",
                        "avatar_url": None,
                        "message": "Earlier",
                        "created_at": created_at,
                        "recipient": None,
                        "private": False,
                        "color": "amber",
                        "attachments": [],
                        "reactions": [],
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
        self.assertEqual(delivered_message["attachments"], [])
        self.assertEqual(delivered_message["reactions"], [])
        self.assertIn("id", delivered_message)
        self.assertIn("timestamp", delivered_message)

        await communicator.disconnect()
