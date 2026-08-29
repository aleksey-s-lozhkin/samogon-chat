import asyncio
from collections import defaultdict

from django.conf import settings


class OnlineUsersService:
    """Tracks WebSocket connections and returns distinct users per room."""

    def __init__(self):
        self._local_connections = defaultdict(dict)
        self._local_all_connections = {}
        self._local_lock = asyncio.Lock()
        self._redis = None

    async def connect(self, *, room_slug: str, channel_name: str, username: str) -> list[str]:
        if settings.REDIS_URL:
            client = await self._get_redis()
            await client.hset(self._redis_key(room_slug), channel_name, username)
            await client.hset(self._redis_all_key(), channel_name, username)
            return await self._redis_users(client, room_slug)

        async with self._local_lock:
            self._local_connections[room_slug][channel_name] = username
            self._local_all_connections[channel_name] = username
            return self._local_users(room_slug)

    async def disconnect(self, *, room_slug: str, channel_name: str) -> list[str]:
        if settings.REDIS_URL:
            client = await self._get_redis()
            await client.hdel(self._redis_key(room_slug), channel_name)
            await client.hdel(self._redis_all_key(), channel_name)
            return await self._redis_users(client, room_slug)

        async with self._local_lock:
            connections = self._local_connections[room_slug]
            connections.pop(channel_name, None)
            self._local_all_connections.pop(channel_name, None)
            if not connections:
                self._local_connections.pop(room_slug, None)
            return self._local_users(room_slug)

    async def get_all_users(self) -> list[str]:
        if settings.REDIS_URL:
            client = await self._get_redis()
            return sorted(set(await client.hvals(self._redis_all_key())))

        async with self._local_lock:
            return sorted(set(self._local_all_connections.values()))

    async def _get_redis(self):
        if self._redis is None:
            import redis.asyncio as redis

            self._redis = redis.from_url(settings.REDIS_URL, decode_responses=True)
        return self._redis

    async def _redis_users(self, client, room_slug: str) -> list[str]:
        return sorted(set(await client.hvals(self._redis_key(room_slug))))

    def _local_users(self, room_slug: str) -> list[str]:
        return sorted(set(self._local_connections[room_slug].values()))

    @staticmethod
    def _redis_key(room_slug: str) -> str:
        return f"samogon:chat:presence:{room_slug}"

    @staticmethod
    def _redis_all_key() -> str:
        return "samogon:chat:presence:all"


online_users = OnlineUsersService()
