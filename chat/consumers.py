import json

from channels.db import database_sync_to_async
from channels.generic.websocket import AsyncWebsocketConsumer

from .services.messages import MessageService
from .validators import validate_message
from .models import Room


online_users = {}


class ChatConsumer(AsyncWebsocketConsumer):
    async def connect(self):
        if self.scope["user"].is_anonymous:
            await self.close()
            return

        self.room_slug = self.scope["url_route"]["kwargs"]["room_slug"]
        self.room_group_name = f"chat_{self.room_slug}"

        await self.channel_layer.group_add(
            self.room_group_name,
            self.channel_name,
        )

        await self.accept()

        user = self.scope["user"]
        messages = await self.get_messages(self.room)

        online_users.setdefault(self.room_name, {})
        online_users[self.room_name][self.channel_name] = user.username

        await self.channel_layer.group_send(
            self.room_group_name,
            {
                "type": "user_status",
                "action": "join",
                "username": user.username,
                "channel_name": self.channel_name,
            },
        )

        await self.broadcast_online_users()
        await self.send_message_history()

        try:
            self.room = await self.get_room(self.room_slug)
        except Room.DoesNotExist:
            await self.close()
            return

    async def disconnect(self, close_code):
        if not hasattr(self, "room_group_name"):
            return

        user = self.scope["user"]

        if not user.is_anonymous:
            room_users = online_users.get(self.room_name, {})

            room_users.pop(self.channel_name, None)

            await self.channel_layer.group_send(
                self.room_group_name,
                {
                    "type": "user_status",
                    "action": "leave",
                    "username": user.username,
                    "channel_name": self.channel_name,
                },
            )

            await self.broadcast_online_users()

        await self.channel_layer.group_discard(
            self.room_group_name,
            self.channel_name,
        )

    async def user_status(self, event):
        if event["channel_name"] == self.channel_name:
            return

        await self.send(
            text_data=json.dumps(
                {
                    "type": "user_status",
                    "action": event["action"],
                    "username": event["username"],
                }
            )
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
            user_id=user.id,
            room_name=self.room_name,
            text=message_text,
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

    async def broadcast_online_users(self):
        users = online_users.get(self.room_name, {})

        await self.channel_layer.group_send(
            self.room_group_name,
            {
                "type": "online_users",
                "users": list(set(users.values())),
            },
        )

    async def online_users(self, event):
        await self.send(
            text_data=json.dumps(
                {
                    "type": "online_users",
                    "users": event["users"],
                }
            )
        )

    @database_sync_to_async
    def create_message(self, *, user_id, room, text):
        return MessageService.create_message(
            user_id=user_id,
            room=room,
            text=text,
        )

    @database_sync_to_async
    def get_messages(self, room):
        return MessageService.get_room_messages(room)

    @database_sync_to_async
    def get_room(self, slug):
        return Room.objects.get(slug=slug)
