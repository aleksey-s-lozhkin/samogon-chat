import json

from channels.db import database_sync_to_async
from channels.generic.websocket import AsyncWebsocketConsumer

from .models import Message
from .validators import validate_message


class ChatConsumer(AsyncWebsocketConsumer):

    async def connect(self):
        if self.scope["user"].is_anonymous:
            await self.close()
            return

        self.room_name = self.scope["url_route"]["kwargs"]["room_name"]
        self.room_group_name = f"chat_{self.room_name}"

        await self.channel_layer.group_add(
            self.room_group_name,
            self.channel_name,
        )

        await self.accept()

        await self.send_message_history()

    async def disconnect(self, close_code):
        if hasattr(self, "room_group_name"):
            await self.channel_layer.group_discard(
                self.room_group_name,
                self.channel_name,
            )

    async def receive(self, text_data):
        message_text, error = validate_message(text_data)

        if error:
            await self.send(
                text_data=json.dumps(
                    {
                        "type": "error",
                        "message": error,
                    }
                )
            )
            return

        user = self.scope["user"]

        message = await self.create_message(
            user.id,
            self.room_name,
            message_text,
        )

        await self.channel_layer.group_send(
            self.room_group_name,
            {
                "type": "chat_message",
                "username": user.username,
                "message": message.text,
                "created_at": message.created_at.isoformat(),
            },
        )

    async def chat_message(self, event):
        await self.send(
            text_data=json.dumps(
                {
                    "type": "message",
                    "username": event["username"],
                    "message": event["message"],
                    "created_at": event["created_at"],
                }
            )
        )

    async def send_message_history(self):
        messages = await self.get_messages(self.room_name)

        await self.send(
            text_data=json.dumps(
                {
                    "type": "history",
                    "messages": messages,
                }
            )
        )

    @database_sync_to_async
    def create_message(self, user_id, room_name, text):
        return Message.objects.create(
            user_id=user_id,
            room_name=room_name,
            text=text,
        )

    @database_sync_to_async
    def get_messages(self, room_name):
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