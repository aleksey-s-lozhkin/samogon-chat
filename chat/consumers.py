import asyncio
import json
from urllib.parse import parse_qs

from asgiref.sync import sync_to_async
from channels.db import database_sync_to_async
from channels.generic.websocket import AsyncWebsocketConsumer
from django.conf import settings
from django.core.exceptions import PermissionDenied
from users.services.safety import blocked_ids, pair_blocked
from chat.services.guests import eligible_guests
from chat.selectors import get_visible_rooms
from django.contrib.auth import get_user_model
from django.db.models import Count, Q
from django.utils import timezone

from config.rate_limit import is_allowed

from .models import Message, Room
from .services.bartender import bartender
from .services.jobs import enqueue_bartender_job
from .services.messages import MessageService
from .services.presence import online_users
from .services.welcome import ensure_welcome_message
from users.models import ChatStatus
from users.services.push import send_direct_message_push
from .validators import validate_message


User = get_user_model()
HISTORY_LIMIT = 50
PRESENCE_GROUP_NAME = "chat_presence"
PUSH_TASKS = set()


def finish_push_task(task) -> None:
    """Убирает завершённую задачу и забирает исключение фоновой операции."""
    PUSH_TASKS.discard(task)
    if not task.cancelled():
        task.exception()


def schedule_direct_message_push(*, recipient_id: int, room_slug: str, sender_id: int | None = None) -> None:
    """Запускает best-effort push, не задерживая WebSocket-ответ."""
    task = asyncio.create_task(
        sync_to_async(send_direct_message_push, thread_sensitive=False)(
            recipient_id=recipient_id,
            room_slug=room_slug,
            sender_id=sender_id,
        )
    )
    PUSH_TASKS.add(task)
    task.add_done_callback(finish_push_task)


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
        await self.touch_last_seen()

        await self.ensure_welcome_message()
        focus_values = parse_qs(
            self.scope.get("query_string", b"").decode("ascii", errors="ignore")
        ).get("focus", [])
        focus_message_id = (
            int(focus_values[0])
            if focus_values and focus_values[0].isdigit()
            else None
        )
        messages, has_more = await self.get_messages(focus_message_id)
        await self.send(
            text_data=json.dumps({
                "type": "history",
                "messages": messages,
                "has_more": has_more,
            })
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
            username=self.user.username,
        )
        await self.touch_last_seen()
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
        if self.room.is_private and not await self.can_access_room(self.user.id):
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
        if isinstance(data, dict) and data.get("type") == "presence_status":
            await self.handle_presence_status(data)
            return
        if isinstance(data, dict) and data.get("type") == "presence_ping":
            removed_stale = await online_users.touch(
                room_slug=self.room.slug,
                channel_name=self.channel_name,
                username=self.user.username,
            )
            if removed_stale:
                users = await online_users.get_room_users(self.room.slug)
                await self.channel_layer.group_send(
                    self.room_group_name,
                    {"type": "online_users", "users": users},
                )
                await self.broadcast_presence()
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
        reply_to_id = data.get("reply_to")
        if reply_to_id is not None and (
            not isinstance(reply_to_id, int) or isinstance(reply_to_id, bool)
        ):
            await self.send_error("Исходная реплика указана некорректно")
            return
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
                await self.send_error("Этот пользователь не участвует в закрытой беседе")
                return

        reply_to = None
        if reply_to_id is not None:
            reply_to = await self.get_reply_target(reply_to_id)
            if reply_to is None:
                await self.send_error("Исходная реплика недоступна")
                return
            if reply_to.recipient_id:
                other_id = (
                    reply_to.recipient_id
                    if reply_to.user_id == self.user.id
                    else reply_to.user_id
                )
                recipient = await self.get_user_by_id(other_id)

        try:
            message = await self.create_message(
                user=self.user,
                text=message_text,
                recipient=recipient,
                reply_to=reply_to,
            )
        except PermissionDenied:
            await self.send_error("Получатель недоступен.")
            return
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
            "reply_to": await self.serialize_reply(message),
            "room_slug": self.room.slug,
            "room_private": self.room.is_private,
        }

        if recipient:
            await self.channel_layer.group_send(self.user_group_name, event)
            await self.channel_layer.group_send(f"chat_user_{recipient.id}", event)
            if recipient.username != settings.BARTENDER_USERNAME:
                schedule_direct_message_push(
                    sender_id=self.user.pk,
                    recipient_id=recipient.id,
                    room_slug=self.room.slug,
                )
            if bartender_question and bartender_private:
                await self.enqueue_bartender_job(message, private=True)
            return

        if self.room.is_private:
            await self.send_to_private_room(event)
        else:
            await self.channel_layer.group_send(self.room_group_name, event)
        await self.broadcast_room_activity()

        if bartender_question:
            await self.enqueue_bartender_job(message, private=False)

    async def chat_message(self, event):
        await self.send_message(event)

    async def direct_message(self, event):
        await self.send_message(event)

    async def room_activity(self, event):
        """Сообщает другим вкладкам о новой общей реплике в комнате."""
        if not await self.visible_activity(event, event.get("username")): return
        await self.send(
            text_data=json.dumps(
                {
                    "type": "room_activity",
                    "username": event["username"],
                    "room_slug": event["room_slug"],
                    "personal": False,
                }
            )
        )

    async def broadcast_room_activity(self):
        event = {
            "type": "room_activity",
            "username": self.user.username,
            "room_slug": self.room.slug,
        }
        if self.room.is_private:
            group_names = [
                f"chat_user_{user_id}"
                for user_id in await self.get_room_member_ids()
            ]
        else:
            group_names = [PRESENCE_GROUP_NAME]
        for group_name in set(group_names):
            await self.channel_layer.group_send(group_name, event)

    async def send_message(self, event):
        """Recheck each recipient at delivery, including queued events."""
        payload = await self.visible_event_message(event["id"])
        if payload is None:
            return
        event = {**event, **payload, "timestamp": payload["created_at"]}
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
                    "reply_to": event.get("reply_to"),
                    "room_slug": event["room_slug"],
                    "room_private": event["room_private"],
                }
            )
        )

    async def attachment_update(self, event):
        """Передаёт клиентам добавленные к уже существующей реплике файлы."""
        payload = await self.visible_event_message(event["message_id"])
        if payload is None: return
        event = {**event, "attachments": payload["attachments"]}
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
        payload = await self.visible_event_message(event["message_id"])
        if payload is None or not await self.visible_activity(event, event.get("actor_username")): return
        reaction = next((item for item in payload["reactions"] if item["emoji"] == event["emoji"]), {"count": 0, "users": []})
        event = {**event, "count": reaction["count"], "users": reaction["users"]}
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
        if not await self.visible_activity(event, event.get("username")): return
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
        names = await self.visible_guest_names()
        event = {**event, "users": [u for u in event["users"] if u in names]}
        await self.send(
            text_data=json.dumps(
                {"type": "online_users", "users": event["users"]}
            )
        )

    async def presence_update(self, event):
        names = await self.visible_guest_names()
        event = {**event, "users": [u for u in event["users"] if u["username"] in names], "online": [u for u in event["online"] if u in names]}
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

    async def handle_presence_status(self, data):
        """Сохраняет выбранный статус и сразу обновляет список гостей."""
        status = data.get("status")
        if not isinstance(status, str) or not await self.is_presence_status_available(status):
            await self.send_error("Такой статус недоступен.")
            return
        if not await self.is_rate_allowed(
            bucket="presence-status",
            limit=settings.PRESENCE_STATUS_RATE_LIMIT,
        ):
            await self.send_error("Статус меняется слишком часто.")
            return

        await self.update_presence_status(status)
        self.user.presence_status = status
        await self.broadcast_presence()

    async def is_rate_allowed(self, *, bucket, limit):
        """Не даёт одному гостю засорять чат или очередь Семёна."""
        return await sync_to_async(is_allowed)(
            identifier=f"user:{self.user.id}",
            bucket=bucket,
            limit=limit,
            window_seconds=settings.RATE_LIMIT_WINDOW_SECONDS,
        )

    async def send_to_private_room(self, event):
        """Доставляет реплику допущенным соединениям закрытой беседы."""
        await self.channel_layer.group_send(self.room_group_name, event)

    async def room_access_revoked(self, event):
        """Закрывает открытую вкладку после выхода или исключения из беседы."""
        if event.get("room_slug") == self.room.slug:
            await self.close(code=4403)

    async def visibility_changed(self, event):
        if not await self.can_access_room(self.user.pk):
            await self.close(code=4403)
            return
        messages, has_more = await self.get_messages()
        await self.send(text_data=json.dumps({"type": "history", "messages": messages, "has_more": has_more}))
        await self.send(text_data=json.dumps({"type": "unread_snapshot", "rooms": await self.unread_snapshot()}))
        await self.presence_update({"users": await self.get_all_users(), "online": await online_users.get_all_users()})

    @database_sync_to_async
    def unread_snapshot(self):
        return {r.slug: MessageService.get_unread_state(room=r, user_id=self.user.pk) for r in get_visible_rooms(self.user)}

    @database_sync_to_async
    def visible_guest_names(self):
        return set(eligible_guests().exclude(pk__in=blocked_ids(self.user.pk)).values_list("username", flat=True))

    @database_sync_to_async
    def visible_activity(self, event, username):
        if not get_visible_rooms(self.user).filter(slug=event.get("room_slug")).exists():
            return False
        author = User.objects.filter(username=username).first()
        if author and author.pk in blocked_ids(self.user.pk):
            return False
        if event.get("recipient") and author and pair_blocked(self.user.pk, author.pk):
            return False
        return True

    @database_sync_to_async
    def visible_event_message(self, message_id):
        message = Message.objects.select_related("room", "user", "recipient", "reply_to__user", "reply_to__recipient").filter(pk=message_id).first()
        if message is None or not MessageService.can_view_message(message=message, user=self.user):
            return None
        return MessageService.serialize_message(message, viewer_id=self.user.pk)

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
            .annotate(
                glasses_poured=Count(
                    "chat_messages",
                    filter=Q(chat_messages__hidden_at__isnull=True),
                )
            )
            .order_by("-glasses_poured", "username")
        )
        status_labels = dict(ChatStatus.objects.values_list("code", "label"))
        return [
            {
                "username": user.username,
                "avatar_url": MessageService.get_avatar_url(user),
                "status": status_labels.get(user.presence_status, user.presence_status),
                "last_seen_at": (
                    user.last_seen_at.isoformat() if user.last_seen_at else None
                ),
            }
            for user in users
        ]

    @database_sync_to_async
    def is_presence_status_available(self, status):
        return not status or ChatStatus.objects.filter(code=status, is_active=True).exists()

    @database_sync_to_async
    def update_presence_status(self, status):
        User.objects.filter(pk=self.user.id).update(presence_status=status)

    @database_sync_to_async
    def touch_last_seen(self):
        User.objects.filter(pk=self.user.id).update(last_seen_at=timezone.now())

    @database_sync_to_async
    def is_chat_restricted(self, user_id):
        """Не пускает в ленту технических и заблокированных аккаунтов."""
        user = User.objects.filter(pk=user_id).first()
        return user is None or user.is_superuser or not user.is_active or user.is_banned

    @database_sync_to_async
    def get_bartender_user(self):
        return bartender.get_bartender_user()

    @database_sync_to_async
    def enqueue_bartender_job(self, message, private):
        return enqueue_bartender_job(
            user=self.user,
            room=self.room,
            question=message,
            private=private,
        )

    @database_sync_to_async
    def ensure_welcome_message(self):
        return ensure_welcome_message(user_id=self.user.id, room=self.room)

    @database_sync_to_async
    def get_messages(self, focus_message_id=None):
        if focus_message_id:
            messages = MessageService.get_room_messages(
                self.room,
                viewer_id=self.user.id,
                limit=HISTORY_LIMIT,
                focus_message_id=focus_message_id,
            )
            return messages, len(messages) > HISTORY_LIMIT
        messages = MessageService.get_room_messages(
            self.room,
            viewer_id=self.user.id,
            limit=HISTORY_LIMIT + 1,
            focus_message_id=focus_message_id,
        )
        has_more = len(messages) > HISTORY_LIMIT
        if has_more:
            messages = messages[-HISTORY_LIMIT:]
        return messages, has_more

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
    def create_message(self, user, text, recipient, reply_to=None):
        """Сохраняет сообщение после проверки WebSocket-пакета."""
        return MessageService.create_message(
            user_id=user.id,
            room=self.room,
            text=text,
            recipient_id=recipient.id if recipient else None,
            reply_to_id=reply_to.id if reply_to else None,
        )

    @database_sync_to_async
    def get_reply_target(self, message_id):
        message = Message.objects.select_related("room", "recipient").filter(
            pk=message_id, room=self.room, hidden_at__isnull=True,
        ).first()
        if message and MessageService.can_view_message(message=message, user=self.user):
            return message
        return None

    @database_sync_to_async
    def get_user_by_id(self, user_id):
        return User.objects.filter(pk=user_id).first()

    @database_sync_to_async
    def serialize_reply(self, message):
        return MessageService.serialize_reply(message, self.user.id)
