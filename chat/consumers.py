import json

from channels.db import database_sync_to_async
from channels.generic.websocket import AsyncWebsocketConsumer
from django.contrib.auth import get_user_model

from .models import Room
from .services.messages import MessageService
from .services.presence import online_users
from .validators import validate_message


User = get_user_model()
HISTORY_LIMIT = 50
PRESENCE_GROUP_NAME = "chat_presence"


class ChatConsumer(AsyncWebsocketConsumer):
    async def connect(self):
        user = self.scope.get("user")
        if not user or user.is_anonymous:
            await self.close(code=4401)
            return

        self.room_slug = self.scope["url_route"]["kwargs"]["room_slug"].lower()
        self.room = await self.get_room(self.room_slug)
        if self.room is None:
            await self.close(code=4404)
            return

        self.user = user
        self.room_group_name = f"chat_{self.room.slug}"
        self.user_group_name = f"chat_user_{user.id}"

        await self.channel_layer.group_add(self.room_group_name, self.channel_name)
        await self.channel_layer.group_add(self.user_group_name, self.channel_name)
        await self.channel_layer.group_add(PRESENCE_GROUP_NAME, self.channel_name)
        await self.accept()

        messages = await self.get_messages()
        await self.send(text_data=json.dumps({"type": "history", "messages": messages}))

        users = await online_users.connect(
            room_slug=self.room.slug,
            channel_name=self.channel_name,
            username=user.username,
        )
        await self.channel_layer.group_send(
            self.room_group_name,
            {"type": "online_users", "users": users},
        )
        await self.broadcast_presence()

    async def disconnect(self, close_code):
        if not hasattr(self, "room_group_name"):
            return

        users = await online_users.disconnect(
            room_slug=self.room.slug,
            channel_name=self.channel_name,
        )
        await self.channel_layer.group_discard(self.room_group_name, self.channel_name)
        await self.channel_layer.group_discard(self.user_group_name, self.channel_name)
        await self.channel_layer.group_discard(PRESENCE_GROUP_NAME, self.channel_name)
        await self.channel_layer.group_send(
            self.room_group_name,
            {"type": "online_users", "users": users},
        )
        await self.broadcast_presence()

    async def receive(self, text_data):
        message_text, error = validate_message(text_data)
        if error:
            await self.send_error(error)
            return

        recipient_username = json.loads(text_data).get("recipient")
        recipient = None
        if recipient_username is not None:
            if not isinstance(recipient_username, str) or not recipient_username.strip():
                await self.send_error("Получатель личного сообщения указан некорректно")
                return

            recipient = await self.get_user(recipient_username.strip())
            if recipient is None:
                await self.send_error("Пользователь не найден")
                return
            if recipient.id == self.user.id:
                await self.send_error("Себе можно написать только в заметки — их пока нет")
                return

        message = await self.create_message(
            user=self.user,
            text=message_text,
            recipient=recipient,
        )
        event = {
            "type": "direct_message" if recipient else "chat_message",
            "username": self.user.username,
            "message": message.text,
            "timestamp": message.created_at.isoformat(),
            "recipient": recipient.username if recipient else None,
            "private": recipient is not None,
            "color": self.user.message_color,
        }

        if recipient:
            await self.channel_layer.group_send(self.user_group_name, event)
            await self.channel_layer.group_send(f"chat_user_{recipient.id}", event)
            return

        await self.channel_layer.group_send(self.room_group_name, event)

    async def chat_message(self, event):
        await self.send_message(event)

    async def direct_message(self, event):
        await self.send_message(event)

    async def send_message(self, event):
        await self.send(
            text_data=json.dumps(
                {
                    "type": "message",
                    "username": event["username"],
                    "message": event["message"],
                    "timestamp": event["timestamp"],
                    "recipient": event["recipient"],
                    "private": event["private"],
                    "color": event["color"],
                }
            )
        )

    async def online_users(self, event):
        await self.send(text_data=json.dumps({"type": "online_users", "users": event["users"]}))

    async def presence_update(self, event):
        await self.send(
            text_data=json.dumps(
                {
                    "type": "user_presence",
                    "users": event["users"],
                    "online": event["online"],
                }
            )
        )

    async def broadcast_presence(self):
        await self.channel_layer.group_send(
            PRESENCE_GROUP_NAME,
            {
                "type": "presence_update",
                "users": await self.get_all_usernames(),
                "online": await online_users.get_all_users(),
            },
        )

    async def send_error(self, message):
        await self.send(text_data=json.dumps({"type": "error", "message": message}))

    @database_sync_to_async
    def get_room(self, room_slug):
        try:
            return Room.objects.get(slug=room_slug)
        except Room.DoesNotExist:
            return None

    @database_sync_to_async
    def get_user(self, username):
        try:
            return User.objects.get(username=username, is_active=True)
        except User.DoesNotExist:
            return None

    @database_sync_to_async
    def get_all_usernames(self):
        return list(User.objects.filter(is_active=True).order_by("username").values_list("username", flat=True))

    @database_sync_to_async
    def get_messages(self):
        return MessageService.get_room_messages(
            self.room,
            viewer_id=self.user.id,
            limit=HISTORY_LIMIT,
        )

    @database_sync_to_async
    def create_message(self, user, text, recipient):
        return MessageService.create_message(
            user_id=user.id,
            room=self.room,
            text=text,
            recipient_id=recipient.id if recipient else None,
        )
