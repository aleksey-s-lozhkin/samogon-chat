from django.conf import settings
from django.db import transaction
from django.db.models import Q
from django.urls import reverse
from django.utils import timezone

from chat.models import (
    Message,
    MessageReaction,
    ModerationEvent,
    Note,
    NoteAttachment,
    Room,
    RoomReadState,
)


class MessageService:
    @staticmethod
    def display_username(username: str) -> str:
        return "Семён" if username == settings.BARTENDER_USERNAME else username

    @staticmethod
    def get_avatar_url(user) -> str | None:
        """Возвращает URL аватара, если гость успел его поставить."""
        return user.avatar.url if user.avatar else None

    @staticmethod
    def create_message(
        *,
        user_id: int,
        room: Room,
        text: str,
        recipient_id: int | None = None,
        reply_to_id: int | None = None,
    ) -> Message:
        return Message.objects.create(
            user_id=user_id,
            room=room,
            text=text,
            recipient_id=recipient_id,
            reply_to_id=reply_to_id,
        )

    @staticmethod
    def hide_message(*, message: Message, actor) -> bool:
        """Скрывает реплику, не уничтожая историю."""
        is_moderator = actor.has_perm("chat.moderate_message")
        if message.user_id != actor.id and not is_moderator:
            return False
        if message.hidden_at is not None:
            return False

        reason = (
            "Скрыто модератором." if is_moderator else "Удалено автором."
        )
        message.hidden_at = timezone.now()
        message.hidden_by = actor
        message.hidden_reason = reason
        message.save(update_fields=("hidden_at", "hidden_by", "hidden_reason"))

        if is_moderator and message.user_id != actor.id:
            ModerationEvent.objects.create(
                action=ModerationEvent.Action.HIDE_MESSAGE,
                moderator=actor,
                target_user_id=message.user_id,
                message=message,
                reason=reason,
            )
        return True

    @staticmethod
    def toggle_reaction(*, message: Message, user, emoji: str) -> tuple[int, bool, list[str]]:
        """Ставит или снимает реакцию, сохраняя одну запись на emoji."""
        if emoji not in MessageReaction.Emoji.values:
            raise ValueError("Такой реакции у стойки пока нет.")
        if not MessageService.can_view_message(message=message, user=user):
            raise PermissionError("Эта реплика вам недоступна.")

        with transaction.atomic():
            reaction, created = MessageReaction.objects.get_or_create(
                message=message,
                user=user,
                emoji=emoji,
            )
            if not created:
                reaction.delete()
            reactions = list(MessageReaction.objects.filter(
                message=message,
                emoji=emoji,
            ).select_related("user"))
        return (
            len(reactions),
            created,
            [MessageService.display_username(item.user.username) for item in reactions],
        )

    @staticmethod
    def save_note(*, user, text: str, source_message: Message | None = None):
        """Хранит копию реплики, чтобы скрытие исходника её не стирало."""
        defaults = {
            "text": source_message.text if source_message else text,
            "source_author": (
                MessageService.display_username(source_message.user.username)
                if source_message
                else ""
            ),
        }
        if source_message:
            note, created = Note.objects.get_or_create(
                user=user,
                source_message=source_message,
                defaults=defaults,
            )
            if created:
                MessageService.copy_note_attachments(
                    note=note,
                    source_message=source_message,
                )
            return note, created
        return Note.objects.create(user=user, **defaults), True

    @staticmethod
    def copy_note_attachments(*, note: Note, source_message: Message) -> None:
        """Копирует вложения, чтобы заметка пережила скрытие исходной реплики."""
        for attachment in source_message.attachments.all():
            copied_attachment = NoteAttachment(
                note=note,
                original_name=attachment.original_name,
                content_type=attachment.content_type,
                size=attachment.size,
                kind=attachment.kind,
            )
            attachment.file.open("rb")
            try:
                copied_attachment.file.save(
                    attachment.original_name,
                    attachment.file,
                    save=False,
                )
            finally:
                attachment.file.close()
            copied_attachment.save()

    @staticmethod
    def serialize_attachment(attachment) -> dict:
        """Не раскрывает путь в хранилище: клиент получает только защищённые URL."""
        preview_url = reverse("chat:attachment", args=[attachment.id])
        return {
            "id": str(attachment.id),
            "name": attachment.original_name,
            "size": attachment.size,
            "kind": attachment.kind,
            "preview_url": preview_url,
            "download_url": reverse("chat:download_attachment", args=[attachment.id]),
        }

    @staticmethod
    def serialize_attachments(message: Message) -> list[dict]:
        return [
            MessageService.serialize_attachment(attachment)
            for attachment in message.attachments.all()
        ]

    @staticmethod
    def can_view_message(*, message: Message, user) -> bool:
        """Не раскрывает личные реплики, тайные комнаты и скрытые сообщения."""
        if not user.is_authenticated or message.hidden_at is not None:
            return False
        if message.recipient_id and user.id not in {
            message.user_id,
            message.recipient_id,
        }:
            return False
        return not message.room.is_private or message.room.memberships.filter(
            user=user,
        ).exists()

    @staticmethod
    def get_room_messages(
        room: Room,
        *,
        viewer_id: int | None = None,
        limit: int | None = None,
    ) -> list[dict]:
        # Личные сообщения видят только отправитель и получатель.
        messages = (
            Message.objects
            .filter(room=room)
            .filter(hidden_at__isnull=True)
            .filter(
                Q(recipient__isnull=True)
                if viewer_id is None
                else Q(recipient__isnull=True)
                | Q(user_id=viewer_id)
                | Q(recipient_id=viewer_id),
            )
            .select_related("user", "recipient", "reply_to__user", "reply_to__recipient")
            .prefetch_related("attachments", "reactions")
            .order_by("created_at")
        )

        if limit is not None:
            messages = messages.order_by("-created_at")[:limit]
            messages = reversed(list(messages))

        return [
            {
                "id": message.id,
                "username": MessageService.display_username(message.user.username),
                "avatar_url": MessageService.get_avatar_url(message.user),
                "message": message.text,
                "created_at": message.created_at.isoformat(),
                "recipient": (
                    MessageService.display_username(message.recipient.username)
                    if message.recipient
                    else None
                ),
                "private": message.recipient_id is not None,
                "color": message.user.message_color,
                "attachments": MessageService.serialize_attachments(message),
                "reactions": MessageService.serialize_reactions(
                    message,
                    viewer_id=viewer_id,
                ),
                "reply_to": MessageService.serialize_reply(message, viewer_id),
            }
            for message in messages
        ]

    @staticmethod
    def serialize_message(message: Message, viewer_id: int | None = None) -> dict:
        return {
            "id": message.id,
            "username": MessageService.display_username(message.user.username),
            "avatar_url": MessageService.get_avatar_url(message.user),
            "message": message.text,
            "created_at": message.created_at.isoformat(),
            "recipient": (
                MessageService.display_username(message.recipient.username)
                if message.recipient
                else None
            ),
            "private": message.recipient_id is not None,
            "color": message.user.message_color,
            "attachments": MessageService.serialize_attachments(message),
            "reactions": MessageService.serialize_reactions(
                message,
                viewer_id=viewer_id,
            ),
            "reply_to": MessageService.serialize_reply(message, viewer_id),
        }

    @staticmethod
    def serialize_reply(message: Message, viewer_id: int | None) -> dict | None:
        source = message.reply_to
        if source is None:
            return None
        unavailable = source.hidden_at is not None or (
            source.recipient_id is not None
            and viewer_id not in {source.user_id, source.recipient_id}
        )
        if viewer_id is None or unavailable:
            return {"id": source.id, "available": False}
        return {
            "id": source.id,
            "available": True,
            "username": MessageService.display_username(source.user.username),
            "message": source.text[:160],
        }

    @staticmethod
    def serialize_reactions(message: Message, viewer_id: int | None = None) -> list[dict]:
        """Собирает счётчики без раскрытия списка участников реакции."""
        reactions = list(message.reactions.all())
        result = []
        for emoji in MessageReaction.Emoji.values:
            emoji_reactions = [item for item in reactions if item.emoji == emoji]
            if not emoji_reactions:
                continue
            result.append(
                {
                    "emoji": emoji,
                    "count": len(emoji_reactions),
                    "reacted": any(item.user_id == viewer_id for item in emoji_reactions),
                    "users": [
                        MessageService.display_username(item.user.username)
                        for item in emoji_reactions
                    ],
                }
            )
        return result

    @staticmethod
    def mark_room_as_read(*, room: Room, user_id: int) -> None:
        """Фиксирует момент открытия комнаты пользователем."""
        RoomReadState.objects.update_or_create(
            room=room,
            user_id=user_id,
            defaults={"last_read_at": timezone.now()},
        )

    @staticmethod
    def get_unread_count(*, room: Room, user_id: int) -> int:
        """Считает непрочитанные личные сообщения или реплики тайной комнаты."""
        read_state = RoomReadState.objects.filter(
            room=room,
            user_id=user_id,
        ).only("last_read_at").first()
        messages = room.messages.exclude(user_id=user_id)
        if read_state:
            messages = messages.filter(created_at__gt=read_state.last_read_at)

        if not room.is_private:
            messages = messages.filter(recipient_id=user_id)
        return messages.count()
