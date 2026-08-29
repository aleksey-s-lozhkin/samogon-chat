from django.db.models import Q

from chat.models import Message, Room


class MessageService:
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
        messages = (
            Message.objects
            .filter(room=room)
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
                "username": message.user.username,
                "message": message.text,
                "created_at": message.created_at.isoformat(),
                "recipient": message.recipient.username if message.recipient else None,
                "private": message.recipient_id is not None,
                "color": message.user.message_color,
            }
            for message in messages
        ]

    @staticmethod
    def serialize_message(message: Message) -> dict:
        return {
            "id": message.id,
            "username": message.user.username,
            "message": message.text,
            "created_at": message.created_at.isoformat(),
            "recipient": message.recipient.username if message.recipient else None,
            "private": message.recipient_id is not None,
            "color": message.user.message_color,
        }
