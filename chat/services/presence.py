import asyncio
import time
from collections import defaultdict

from django.conf import settings


class OnlineUsersService:
    """Хранит только живые WebSocket-подключения пользователей."""

    MEMBER_SEPARATOR = "\x1f"

    def __init__(self):
        self._local_connections = defaultdict(dict)
        self._local_all_connections = {}
        self._local_lock = asyncio.Lock()
        self._redis = None

    @property
    def ttl_seconds(self) -> int:
        return getattr(settings, "PRESENCE_TTL_SECONDS", 75)

    async def connect(self, *, room_slug, channel_name, username):
        if settings.REDIS_URL:
            client = await self._get_redis()
            await self._redis_touch(client, room_slug, channel_name, username)
            return await self._redis_users(client, room_slug)

        async with self._local_lock:
            expires_at = time.time() + self.ttl_seconds
            self._local_connections[room_slug][channel_name] = (username, expires_at)
            self._local_all_connections[channel_name] = (username, expires_at)
            self._prune_local()
            return self._local_users(room_slug)

    async def touch(self, *, room_slug, channel_name, username):
        """Продлевает присутствие активного WebSocket-соединения."""
        if settings.REDIS_URL:
            client = await self._get_redis()
            return await self._redis_touch(
                client,
                room_slug,
                channel_name,
                username,
            )

        async with self._local_lock:
            if channel_name not in self._local_connections.get(room_slug, {}):
                return False
            removed_stale = self._prune_local()
            expires_at = time.time() + self.ttl_seconds
            self._local_connections[room_slug][channel_name] = (username, expires_at)
            self._local_all_connections[channel_name] = (username, expires_at)
            return removed_stale

    async def disconnect(self, *, room_slug, channel_name, username):
        if settings.REDIS_URL:
            client = await self._get_redis()
            member = self._member(channel_name, username)
            await client.zrem(self._redis_key(room_slug), member)
            await client.zrem(self._redis_all_key(), member)
            return await self._redis_users(client, room_slug)

        async with self._local_lock:
            connections = self._local_connections[room_slug]
            connections.pop(channel_name, None)
            self._local_all_connections.pop(channel_name, None)
            if not connections:
                self._local_connections.pop(room_slug, None)
            self._prune_local()
            return self._local_users(room_slug)

    async def get_all_users(self):
        if settings.REDIS_URL:
            client = await self._get_redis()
            return await self._redis_users_for_key(client, self._redis_all_key())

        async with self._local_lock:
            self._prune_local()
            return sorted(
                {username for username, _expires_at in self._local_all_connections.values()}
            )

    async def get_room_users(self, room_slug):
        """Возвращает живых пользователей одной беседы."""
        if settings.REDIS_URL:
            client = await self._get_redis()
            return await self._redis_users(client, room_slug)

        async with self._local_lock:
            self._prune_local()
            return self._local_users(room_slug)

    async def _get_redis(self):
        if self._redis is None:
            import redis.asyncio as redis

            self._redis = redis.from_url(settings.REDIS_URL, decode_responses=True)
        return self._redis

    async def _redis_touch(self, client, room_slug, channel_name, username):
        now = time.time()
        removed_from_room = await client.zremrangebyscore(
            self._redis_key(room_slug),
            "-inf",
            now,
        )
        removed_from_all = await client.zremrangebyscore(
            self._redis_all_key(),
            "-inf",
            now,
        )
        expires_at = now + self.ttl_seconds
        member = self._member(channel_name, username)
        await client.zadd(self._redis_key(room_slug), {member: expires_at})
        await client.zadd(self._redis_all_key(), {member: expires_at})
        await client.expire(self._redis_key(room_slug), self.ttl_seconds * 2)
        await client.expire(self._redis_all_key(), self.ttl_seconds * 2)
        return bool(removed_from_room or removed_from_all)

    async def _redis_users(self, client, room_slug):
        return await self._redis_users_for_key(client, self._redis_key(room_slug))

    async def _redis_users_for_key(self, client, key):
        await client.zremrangebyscore(key, "-inf", time.time())
        members = await client.zrange(key, 0, -1)
        return sorted({self._username(member) for member in members})

    def _prune_local(self):
        now = time.time()
        removed_stale = False
        for room_slug, connections in list(self._local_connections.items()):
            for channel_name, (_username, expires_at) in list(connections.items()):
                if expires_at <= now:
                    removed_stale = True
                    connections.pop(channel_name, None)
                    self._local_all_connections.pop(channel_name, None)
            if not connections:
                self._local_connections.pop(room_slug, None)

        for channel_name, (_username, expires_at) in list(
            self._local_all_connections.items()
        ):
            if expires_at <= now:
                removed_stale = True
                self._local_all_connections.pop(channel_name, None)
        return removed_stale

    def _local_users(self, room_slug):
        return sorted(
            {
                username
                for username, _expires_at in self._local_connections[room_slug].values()
            }
        )

    @classmethod
    def _member(cls, channel_name, username):
        return f"{channel_name}{cls.MEMBER_SEPARATOR}{username}"

    @classmethod
    def _username(cls, member):
        return member.rsplit(cls.MEMBER_SEPARATOR, 1)[-1]

    @staticmethod
    def _redis_key(room_slug):
        return f"samogon:chat:presence:v2:{room_slug}"

    @staticmethod
    def _redis_all_key():
        return "samogon:chat:presence:v2:all"


online_users = OnlineUsersService()
