import json

from asgiref.sync import sync_to_async
from channels.db import database_sync_to_async
from channels.generic.websocket import AsyncWebsocketConsumer
from django.conf import settings
from django.contrib.auth import get_user_model
from django.db.models import Q
from django.utils import timezone

from config.rate_limit import is_allowed

from .models import Message, Room
from .services.bartender import BartenderUnavailable, bartender
from .services.messages import MessageService
from .services.presence import online_users
from .validators import validate_message


User = get_user_model()
HISTORY_LIMIT = 50
PRESENCE_GROUP_NAME = "chat_presence"


class ChatConsumer(AsyncWebsocketConsumer):
    async def connect(self):
        """Подключает авторизованного пользователя к комнате и presence-каналам.

        Presence-каналы нужны для списка пользователей онлайн.
        """
        user = self.scope.get("user")
        if not user or user.is_anonymous:
            await self.close(code=4401)
            return
        if await self.is_chat_restricted(user.id):
            await self.close(code=4403)
            return

        self.room_slug = self.scope["url_route"]["kwargs"]["room_slug"].lower()
        self.room = await self.get_room(self.room_slug)
        if self.room is None:
            await self.close(code=4404)
            return
        if not await self.can_access_room(user.id):
            await self.close(code=4403)
            return

        self.user = user
        self.room_group_name = f"chat_{self.room.slug}"
        self.user_group_name = f"chat_user_{user.id}"

        await self.channel_layer.group_add(self.room_group_name, self.channel_name)
        await self.channel_layer.group_add(self.user_group_name, self.channel_name)
        await self.channel_layer.group_add(PRESENCE_GROUP_NAME, self.channel_name)
        await self.accept()

        messages = await self.get_messages()
        await self.send(
            text_data=json.dumps({"type": "history", "messages": messages})
        )

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
        await self.channel_layer.group_discard(
            self.room_group_name,
            self.channel_name,
        )
        await self.channel_layer.group_discard(
            self.user_group_name,
            self.channel_name,
        )
        await self.channel_layer.group_discard(PRESENCE_GROUP_NAME, self.channel_name)
        await self.channel_layer.group_send(
            self.room_group_name,
            {"type": "online_users", "users": users},
        )
        await self.broadcast_presence()

    async def receive(self, text_data):
        """Сохраняет сообщение и рассылает его адресатам.

        Личное обращение к Семёну не попадает в общий канал комнаты.
        """
        if await self.is_chat_restricted(self.user.id):
            await self.close(code=4403)
            return

        try:
            data = json.loads(text_data)
        except json.JSONDecodeError:
            data = None

        if isinstance(data, dict) and data.get("type") == "reaction":
            await self.handle_reaction(data)
            return
        if isinstance(data, dict) and data.get("type") == "typing":
            await self.handle_typing(data)
            return

        if not await self.is_rate_allowed(
            bucket="message",
            limit=settings.MESSAGE_RATE_LIMIT,
        ):
            await self.send_error("Слишком много сообщений. Подождите минуту.")
            return

        message_text, error = validate_message(text_data)
        if error:
            await self.send_error(error)
            return

        data = json.loads(text_data)
        recipient_username = data.get("recipient")
        bartender_private = data.get("bartender_private", False)
        if not isinstance(bartender_private, bool):
            await self.send_error(
                "Настройка видимости вопроса Семёну указана некорректно"
            )
            return

        bartender_question = bartender.is_mentioned(message_text)
        if bartender_question and not await self.is_rate_allowed(
            bucket="bartender",
            limit=settings.BARTENDER_RATE_LIMIT,
        ):
            await self.send_error("Семён занят у стойки. Попробуйте через минуту.")
            return

        if bartender_private and not bartender_question:
            await self.send_error("Личным может быть только вопрос Семёну")
            return
        if bartender_private and recipient_username is not None:
            await self.send_error(
                "Выберите либо личное сообщение, либо вопрос Семёну"
            )
            return

        recipient = None
        if bartender_private:
            recipient = await self.get_bartender_user()
        elif recipient_username is not None:
            if (
                not isinstance(recipient_username, str)
                or not recipient_username.strip()
            ):
                await self.send_error(
                    "Получатель личного сообщения указан некорректно"
                )
                return

            recipient = await self.get_user(recipient_username.strip())
            if recipient is None:
                await self.send_error("Пользователь не найден")
                return
            if recipient.id == self.user.id:
                await self.send_error(
                    "Себе можно написать только в заметки — их пока нет"
                )
                return
            if self.room.is_private and not await self.is_room_member(recipient.id):
                await self.send_error("Этот гость не сидит за вашим тайным столиком")
                return

        message = await self.create_message(
            user=self.user,
            text=message_text,
            recipient=recipient,
        )
        event = {
            "type": "direct_message" if recipient else "chat_message",
            "id": message.id,
            "username": self.user.username,
            "avatar_url": MessageService.get_avatar_url(self.user),
            "message": message.text,
            "timestamp": message.created_at.isoformat(),
            "recipient": (
                "Семён"
                if bartender_private
                else recipient.username if recipient else None
            ),
            "private": recipient is not None,
            "color": self.user.message_color,
            "attachments": [],
            "reactions": [],
            "room_slug": self.room.slug,
            "room_private": self.room.is_private,
        }

        if recipient:
            await self.channel_layer.group_send(self.user_group_name, event)
            await self.channel_layer.group_send(f"chat_user_{recipient.id}", event)
            if bartender_question and bartender_private:
                await self.reply_as_bartender(message_text, recipient=self.user)
            return

        if self.room.is_private:
            await self.send_to_private_room(event)
        else:
            await self.channel_layer.group_send(self.room_group_name, event)

        if bartender_question:
            await self.reply_as_bartender(message_text)

    async def chat_message(self, event):
        await self.send_message(event)

    async def direct_message(self, event):
        await self.send_message(event)

    async def send_message(self, event):
        """Преобразует событие Channels в формат сообщения клиента."""
        await self.send(
            text_data=json.dumps(
                {
                    "type": "message",
                    "id": event["id"],
                    "username": event["username"],
                    "avatar_url": event.get("avatar_url"),
                    "message": event["message"],
                    "timestamp": event["timestamp"],
                    "recipient": event["recipient"],
                    "private": event["private"],
                    "color": event["color"],
                    "attachments": event.get("attachments", []),
                    "reactions": event.get("reactions", []),
                    "room_slug": event["room_slug"],
                    "room_private": event["room_private"],
                }
            )
        )

    async def attachment_update(self, event):
        """Передаёт клиентам добавленные к уже существующей реплике файлы."""
        await self.send(
            text_data=json.dumps(
                {
                    "type": "attachments",
                    "message_id": event["message_id"],
                    "attachments": event["attachments"],
                    "room_slug": event["room_slug"],
                }
            )
        )

    async def message_deleted(self, event):
        """Сообщает клиенту, что реплику скрыли вне WebSocket-соединения."""
        await self.send(
            text_data=json.dumps(
                {
                    "type": "message_deleted",
                    "message_id": event["message_id"],
                    "room_slug": event["room_slug"],
                }
            )
        )

    async def reaction_update(self, event):
        """Рассылает новый счётчик только тем, кто видит исходную реплику."""
        await self.send(
            text_data=json.dumps(
                {
                    "type": "reaction_update",
                    "message_id": event["message_id"],
                    "emoji": event["emoji"],
                    "count": event["count"],
                    "users": event["users"],
                    "active": event["active"],
                    "actor_username": event["actor_username"],
                    "room_slug": event["room_slug"],
                }
            )
        )

    async def typing_update(self, event):
        """Передаёт краткоживущий индикатор набора без сохранения в БД."""
        await self.send(
            text_data=json.dumps(
                {
                    "type": "typing_update",
                    "username": event["username"],
                    "active": event["active"],
                    "recipient": event.get("recipient"),
                    "room_slug": event["room_slug"],
                }
            )
        )

    async def online_users(self, event):
        await self.send(
            text_data=json.dumps(
                {"type": "online_users", "users": event["users"]}
            )
        )

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
                "users": await self.get_all_users(),
                "online": await online_users.get_all_users(),
            },
        )

    async def send_error(self, message):
        await self.send(
            text_data=json.dumps({"type": "error", "message": message})
        )

    async def handle_reaction(self, data):
        """Обрабатывает реакцию отдельно от отправки текстовой реплики."""
        message_id = data.get("message_id")
        emoji = data.get("emoji")
        if not isinstance(message_id, int) or not isinstance(emoji, str):
            await self.send_error("Реакция указана некорректно.")
            return
        if not await self.is_rate_allowed(
            bucket="reaction",
            limit=settings.REACTION_RATE_LIMIT,
        ):
            await self.send_error("Слишком много реакций. Сделайте глоток паузы.")
            return

        result = await self.toggle_reaction(message_id, emoji)
        if result is None:
            await self.send_error("Эта реплика вам недоступна.")
            return

        event = {
            "type": "reaction_update",
            "message_id": message_id,
            "emoji": emoji,
            "count": result["count"],
            "users": result["users"],
            "active": result["active"],
            "actor_username": self.user.username,
            "room_slug": self.room.slug,
        }
        if result["recipient_id"]:
            group_names = [
                f"chat_user_{result['author_id']}",
                f"chat_user_{result['recipient_id']}",
            ]
        elif self.room.is_private:
            group_names = [
                f"chat_user_{user_id}"
                for user_id in await self.get_room_member_ids()
            ]
        else:
            group_names = [self.room_group_name]

        for group_name in set(group_names):
            await self.channel_layer.group_send(group_name, event)

    async def handle_typing(self, data):
        """Рассылает набор текста только тем, кто увидел бы будущую реплику."""
        active = data.get("active")
        recipient_username = data.get("recipient")
        if not isinstance(active, bool):
            return
        if recipient_username is not None and (
            not isinstance(recipient_username, str) or not recipient_username.strip()
        ):
            return
        if not await self.is_rate_allowed(
            bucket="typing",
            limit=settings.TYPING_RATE_LIMIT,
        ):
            return

        recipient = None
        if recipient_username is not None:
            recipient = await self.get_user(recipient_username.strip())
            if recipient is None or recipient.id == self.user.id:
                return
            if self.room.is_private and not await self.is_room_member(recipient.id):
                return

        event = {
            "type": "typing_update",
            "username": self.user.username,
            "active": active,
            "recipient": recipient.username if recipient else None,
            "room_slug": self.room.slug,
        }
        if recipient:
            group_names = [self.user_group_name, f"chat_user_{recipient.id}"]
        elif self.room.is_private:
            group_names = [
                f"chat_user_{user_id}"
                for user_id in await self.get_room_member_ids()
            ]
        else:
            group_names = [self.room_group_name]

        for group_name in set(group_names):
            await self.channel_layer.group_send(group_name, event)

    async def is_rate_allowed(self, *, bucket, limit):
        """Не даёт одному гостю засорять чат или очередь Семёна."""
        return await sync_to_async(is_allowed)(
            identifier=f"user:{self.user.id}",
            bucket=bucket,
            limit=limit,
            window_seconds=settings.RATE_LIMIT_WINDOW_SECONDS,
        )

    async def reply_as_bartender(self, message_text, recipient=None):
        try:
            reply = await self.get_bartender_reply(message_text)
        except BartenderUnavailable:
            await self.send_error(
                "Семён сейчас отошёл от стойки. Попробуйте чуть позже."
            )
            return

        bartender_user = await self.get_bartender_user()
        message = await self.create_message(
            user=bartender_user,
            text=reply,
            recipient=recipient,
        )
        event = {
            "type": "direct_message" if recipient else "chat_message",
            "id": message.id,
            "username": "Семён",
            "avatar_url": MessageService.get_avatar_url(bartender_user),
            "message": message.text,
            "timestamp": message.created_at.isoformat(),
            "recipient": recipient.username if recipient else None,
            "private": recipient is not None,
            "color": "amber",
            "attachments": [],
            "reactions": [],
            "room_slug": self.room.slug,
            "room_private": self.room.is_private,
        }
        if recipient:
            await self.channel_layer.group_send(f"chat_user_{recipient.id}", event)
            return

        if self.room.is_private:
            await self.send_to_private_room(event)
        else:
            await self.channel_layer.group_send(self.room_group_name, event)

    async def send_to_private_room(self, event):
        """Доставляет реплику всем участникам тайного столика."""
        for user_id in await self.get_room_member_ids():
            await self.channel_layer.group_send(
                f"chat_user_{user_id}",
                event,
            )

    @database_sync_to_async
    def get_room(self, room_slug):
        try:
            return Room.objects.get(slug=room_slug)
        except Room.DoesNotExist:
            return None

    @database_sync_to_async
    def can_access_room(self, user_id):
        if not self.room.is_private:
            return True
        return self.room.memberships.filter(user_id=user_id).exists()

    @database_sync_to_async
    def is_room_member(self, user_id):
        return self.room.memberships.filter(user_id=user_id).exists()

    @database_sync_to_async
    def get_room_member_ids(self):
        return list(self.room.memberships.values_list("user_id", flat=True))

    @database_sync_to_async
    def get_user(self, username):
        try:
            return (
                User.objects.filter(username=username, is_active=True)
                .filter(
                    Q(banned_at__isnull=True)
                    | Q(banned_until__lte=timezone.now())
                )
                .exclude(is_superuser=True)
                .get()
            )
        except User.DoesNotExist:
            return None

    @database_sync_to_async
    def get_all_users(self):
        """Возвращает гостей для списка присутствующих без техаккаунта."""
        users = (
            User.objects.filter(is_active=True, is_superuser=False)
            .filter(
                Q(banned_at__isnull=True)
                | Q(banned_until__lte=timezone.now())
            )
            .exclude(username=settings.BARTENDER_USERNAME)
            .order_by("username")
        )
        return [
            {
                "username": user.username,
                "avatar_url": MessageService.get_avatar_url(user),
            }
            for user in users
        ]

    @database_sync_to_async
    def is_chat_restricted(self, user_id):
        """Не пускает в ленту технических и заблокированных аккаунтов."""
        user = User.objects.filter(pk=user_id).first()
        return user is None or user.is_superuser or not user.is_active or user.is_banned

    @database_sync_to_async
    def get_bartender_reply(self, message_text):
        return bartender.reply(
            room_name=self.room.name,
            username=self.user.username,
            text=message_text,
        ).text

    @database_sync_to_async
    def get_bartender_user(self):
        return bartender.get_bartender_user()

    @database_sync_to_async
    def get_messages(self):
        return MessageService.get_room_messages(
            self.room,
            viewer_id=self.user.id,
            limit=HISTORY_LIMIT,
        )

    @database_sync_to_async
    def toggle_reaction(self, message_id, emoji):
        try:
            message = Message.objects.select_related("room", "recipient").get(
                id=message_id,
                room=self.room,
                hidden_at__isnull=True,
            )
        except Message.DoesNotExist:
            return None
        try:
            count, active, users = MessageService.toggle_reaction(
                message=message,
                user=self.user,
                emoji=emoji,
            )
        except (PermissionError, ValueError):
            return None
        return {
            "count": count,
            "active": active,
            "users": users,
            "author_id": message.user_id,
            "recipient_id": message.recipient_id,
        }

    @database_sync_to_async
    def create_message(self, user, text, recipient):
        """Сохраняет сообщение после проверки WebSocket-пакета."""
        return MessageService.create_message(
            user_id=user.id,
            room=self.room,
            text=text,
            recipient_id=recipient.id if recipient else None,
        )
