"""Per-application rate limiting and daily quota.

Fixed-window counters, one INCR per request. Counters live in Redis when it is
enabled, so every replica sees the same numbers; without Redis each process
counts on its own and the effective limit becomes `limit x replicas`.
"""

import logging
from dataclasses import dataclass
from time import time

from app.cache.redis import get_redis

logger = logging.getLogger(__name__)

NAMESPACE = "ai_service"

MINUTE_SECONDS = 60
DAY_SECONDS = 86_400


@dataclass(frozen=True)
class LimitDecision:
    allowed: bool = True
    scope: str = ""
    limit: int | None = None
    remaining: int | None = None
    retry_after: int | None = None


class RateLimiter:
    def __init__(self, namespace: str = NAMESPACE) -> None:
        self._namespace = namespace
        self._memory: dict[str, tuple[int, float]] = {}

    async def check(
        self,
        app_id: str,
        *,
        per_minute: int | None = None,
        per_day: int | None = None,
    ) -> LimitDecision:
        decision = LimitDecision(allowed=True)

        if per_minute:
            decision = await self._hit(
                f"{self._namespace}:rl:{app_id}", per_minute, MINUTE_SECONDS, "rate"
            )
            if not decision.allowed:
                return decision

        if per_day:
            decision = await self._hit(
                f"{self._namespace}:quota:{app_id}", per_day, DAY_SECONDS, "quota"
            )
            if not decision.allowed:
                return decision

        return decision

    async def _hit(
        self,
        prefix: str,
        limit: int,
        window_seconds: int,
        scope: str,
    ) -> LimitDecision:
        now = time()
        bucket = int(now // window_seconds)
        key = f"{prefix}:{bucket}"

        count = await self._increment(key, window_seconds)
        if count <= limit:
            return LimitDecision(
                allowed=True,
                scope=scope,
                limit=limit,
                remaining=max(0, limit - count),
            )

        retry_after = window_seconds - int(now % window_seconds)
        return LimitDecision(
            allowed=False,
            scope=scope,
            limit=limit,
            remaining=0,
            retry_after=max(1, retry_after),
        )

    async def _increment(self, key: str, window_seconds: int) -> int:
        client = await get_redis()
        if client is not None:
            try:
                count = int(await client.incr(key))
                if count == 1:
                    # Slight overshoot so the key cannot expire mid-window.
                    await client.expire(key, window_seconds + 5)
                return count
            except Exception:
                logger.warning("Redis counter failed; using in-process counters", exc_info=True)

        return self._increment_memory(key, window_seconds)

    def _increment_memory(self, key: str, window_seconds: int) -> int:
        now = time()

        if len(self._memory) > 5_000:
            self._memory = {name: entry for name, entry in self._memory.items() if entry[1] > now}

        count, expires_at = self._memory.get(key, (0, now + window_seconds))
        if expires_at <= now:
            count, expires_at = 0, now + window_seconds

        count += 1
        self._memory[key] = (count, expires_at)
        return count

    def reset(self) -> None:
        self._memory.clear()


rate_limiter = RateLimiter()
