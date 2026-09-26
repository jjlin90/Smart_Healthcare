import json
from contextlib import asynccontextmanager
from uuid import uuid4
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
        self._turns: TTLCache[str, str] = TTLCache(maxsize=1000, ttl=settings.request_timeout_seconds + 60)

    @asynccontextmanager
    async def turn(self, patient_id: str, conversation_id: str):
        key = "medagent:turn:" + self._key(patient_id, conversation_id)
        owner = uuid4().hex
        if self._redis:
            acquired = await self._redis.set(key, owner, nx=True, ex=get_settings().request_timeout_seconds + 60)
        else:
            acquired = key not in self._turns
            if acquired:
                self._turns[key] = owner
        if not acquired:
            raise RuntimeError("该会话已有任务执行中，请等待完成")
        try:
            yield
        finally:
            if self._redis:
                await self._redis.eval(
                    "if redis.call('GET',KEYS[1])==ARGV[1] then return redis.call('DEL',KEYS[1]) end; return 0",
                    1, key, owner,
                )
            elif self._turns.get(key) == owner:
                del self._turns[key]

    async def append_turn(self, patient_id: str, conversation_id: str, question: str, answer: str) -> None:
        key = self._key(patient_id, conversation_id)
        pair = [{"role": "user", "content": question}, {"role": "assistant", "content": answer}]
        if self._redis:
            redis_key = f"medagent:session:{key}"
            pipeline = self._redis.pipeline(transaction=True)
            pipeline.rpush(redis_key, *(json.dumps(item, ensure_ascii=False) for item in pair))
            pipeline.ltrim(redis_key, -20, -1)
            pipeline.expire(redis_key, get_settings().session_ttl_seconds)
            await pipeline.execute()
        else:
            self._sessions[key] = ((await self.get(patient_id, conversation_id)) + pair)[-20:]

    @staticmethod
    def _key(patient_id: str, conversation_id: str) -> str:
        return json.dumps([patient_id, conversation_id], ensure_ascii=True, separators=(",", ":"))

    async def get(self, patient_id: str, conversation_id: str) -> list[dict[str, str]]:
        key = self._key(patient_id, conversation_id)
        if self._redis:
            values = await self._redis.lrange(f"medagent:session:{key}", 0, -1)
            return [json.loads(value) for value in values]
        return deepcopy(self._sessions.get(key, []))

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

    async def close(self) -> None:
        if self._redis:
            await self._redis.aclose()


conversation_memory = ConversationMemory()
