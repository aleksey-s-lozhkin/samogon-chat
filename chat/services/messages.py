from chat.models import Message, Room


class MessageService:
    @staticmethod
    def create_message(
        *,
        user_id: int,
        room: Room,
        text: str,
    ) -> Message:
        return Message.objects.create(
            user_id=user_id,
            room=room,
            text=text,
        )

    @staticmethod
    def get_room_messages(room: Room) -> list[dict]:
        messages = (
            Message.objects
            .filter(room=room)
            .select_related("user")
        )

        return [
            {
                "username": message.user.username,
                "message": message.text,
                "created_at": message.created_at.isoformat(),
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
        }
