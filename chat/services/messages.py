from django.conf import settings
from django.db.models import Q
from django.utils import timezone

from chat.models import Message, Room, RoomReadState


class MessageService:
    @staticmethod
    def display_username(username: str) -> str:
        return "Семён" if username == settings.BARTENDER_USERNAME else username

    @staticmethod
    def create_message(
        *,
        user_id: int,
        room: Room,
        text: str,
        recipient_id: int | None = None,
    ) -> Message:
        return Message.objects.create(
            user_id=user_id,
            room=room,
            text=text,
            recipient_id=recipient_id,
        )

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
            .select_related("user", "recipient")
            .order_by("created_at")
        )

        if limit is not None:
            messages = messages.order_by("-created_at")[:limit]
            messages = reversed(list(messages))

        return [
            {
                "username": MessageService.display_username(message.user.username),
                "message": message.text,
                "created_at": message.created_at.isoformat(),
                "recipient": (
                    MessageService.display_username(message.recipient.username)
                    if message.recipient
                    else None
                ),
                "private": message.recipient_id is not None,
                "color": message.user.message_color,
            }
            for message in messages
        ]

    @staticmethod
    def serialize_message(message: Message) -> dict:
        return {
            "id": message.id,
            "username": MessageService.display_username(message.user.username),
            "message": message.text,
            "created_at": message.created_at.isoformat(),
            "recipient": (
                MessageService.display_username(message.recipient.username)
                if message.recipient
                else None
            ),
            "private": message.recipient_id is not None,
            "color": message.user.message_color,
        }

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
