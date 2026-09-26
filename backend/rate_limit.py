"""Bounded login attempts; Redis counters are shared across API replicas."""

import hashlib
import time
from cachetools import TTLCache
from redis.asyncio import Redis

from backend.config import get_settings


class LoginLimiter:
    def __init__(self):
        self.local = TTLCache(maxsize=10000, ttl=3600)
        self.redis = None
        self.redis_url = None

    async def allow(self, employee_id: str, client_ip: str) -> bool:
        settings = get_settings()
        window = settings.login_window_seconds
        slot = int(time.time()) // window
        keys = [
            (f"account:{employee_id.strip()}", settings.login_max_attempts),
            (f"source:{client_ip}", settings.login_max_attempts * 10),
        ]
        allowed = True
        if settings.redis_url and self.redis_url != settings.redis_url:
            self.redis = Redis.from_url(settings.redis_url)
            self.redis_url = settings.redis_url
        for value, limit in keys:
            key = f"medagent:login:{slot}:" + hashlib.sha256(value.encode()).hexdigest()
            if settings.redis_url:
                count = await self.redis.eval(
                    "local n=redis.call('INCR',KEYS[1]); if n==1 then redis.call('EXPIRE',KEYS[1],ARGV[1]) end; return n",
                    1, key, window * 2,
                )
            else:
                count = self.local.get(key, 0) + 1
                self.local[key] = count
            allowed = allowed and count <= limit
        return allowed

    async def close(self) -> None:
        if self.redis:
            await self.redis.aclose()
            self.redis = None
            self.redis_url = None


login_limiter = LoginLimiter()
