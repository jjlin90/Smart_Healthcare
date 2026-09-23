import json
from copy import deepcopy
from typing import Any

from cachetools import TTLCache
from redis.asyncio import Redis

from backend.config import get_settings


class ConversationMemory:
    """Isolated short-term memory keyed by patient and conversation."""

    def __init__(self, *, use_redis: bool = True) -> None:
        settings = get_settings()
        self._redis = Redis.from_url(settings.redis_url, decode_responses=True) if use_redis and settings.redis_url else None
        self._sessions: TTLCache[str, list[dict[str, str]]] = TTLCache(
            maxsize=100,
            ttl=settings.session_ttl_seconds,
        )
        self._checkpoints: TTLCache[str, dict[str, Any]] = TTLCache(
            maxsize=500,
            ttl=settings.checkpoint_ttl_seconds,
        )

    @staticmethod
    def _key(patient_id: str, conversation_id: str) -> str:
        return json.dumps([patient_id, conversation_id], ensure_ascii=True, separators=(",", ":"))

    async def get(self, patient_id: str, conversation_id: str) -> list[dict[str, str]]:
        key = self._key(patient_id, conversation_id)
        if self._redis:
            values = await self._redis.lrange(f"medagent:session:{key}", 0, -1)
            return [json.loads(value) for value in values]
        if key not in self._sessions:
            self._sessions[key] = []
        return deepcopy(self._sessions[key])

    async def append(
        self,
        patient_id: str,
        conversation_id: str,
        role: str,
        content: str,
    ) -> None:
        key = self._key(patient_id, conversation_id)
        if self._redis:
            redis_key = f"medagent:session:{key}"
            pipeline = self._redis.pipeline(transaction=True)
            pipeline.rpush(redis_key, json.dumps({"role": role, "content": content}, ensure_ascii=False))
            pipeline.ltrim(redis_key, -20, -1)
            pipeline.expire(redis_key, get_settings().session_ttl_seconds)
            await pipeline.execute()
            return
        history = await self.get(patient_id, conversation_id)
        history.append({"role": role, "content": content})
        del history[:-20]
        self._sessions[key] = history  # Refresh sliding TTL, matching Redis EXPIRE.

    async def save_checkpoint(self, patient_id: str, conversation_id: str, state: dict[str, Any]) -> None:
        key = self._key(patient_id, conversation_id)
        if self._redis:
            await self._redis.set(
                f"medagent:checkpoint:{key}",
                json.dumps(state, ensure_ascii=False),
                ex=get_settings().checkpoint_ttl_seconds,
            )
            return
        self._checkpoints[key] = deepcopy(state)

    async def get_checkpoint(self, patient_id: str, conversation_id: str) -> dict[str, Any] | None:
        key = self._key(patient_id, conversation_id)
        if self._redis:
            value = await self._redis.get(f"medagent:checkpoint:{key}")
            return json.loads(value) if value else None
        return deepcopy(self._checkpoints.get(key))

    async def ping(self) -> bool:
        if self._redis:
            return bool(await self._redis.ping())
        return True

    @property
    def backend_name(self) -> str:
        return "redis" if self._redis else "local_ttl"


conversation_memory = ConversationMemory()
