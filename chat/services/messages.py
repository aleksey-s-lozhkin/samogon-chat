from chat.models import Message


class MessageService:
    @staticmethod
    def create_message(*, user_id: int, room_name: str, text: str) -> Message:
        return Message.objects.create(
            user_id=user_id,
            room_name=room_name,
            text=text,
        )

    @staticmethod
    def get_room_messages(room_name: str) -> list[dict]:
        messages = (
            Message.objects
            .filter(room_name=room_name)
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