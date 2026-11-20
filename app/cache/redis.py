"""Shared Redis client.

`REDIS_BROKER_ENABLED` is the same switch Aegra uses for its task queue and SSE
pubsub, so one flag turns the whole service from single-instance to multi-replica.

Every caller must handle `None`: without Redis the service still works, it just
loses cross-replica coordination, and the caller falls back to process-local
state. A dead Redis must never take the API down.
"""

import logging
from time import monotonic

from redis.asyncio import Redis

from app.config import get_settings

logger = logging.getLogger(__name__)

#: Retry a failed connection after this long instead of hammering it per request.
_RETRY_COOLDOWN_SECONDS = 30.0

_client: Redis | None = None
_retry_at: float = 0.0


async def get_redis() -> Redis | None:
    global _client, _retry_at

    settings = get_settings()
    if not settings.redis_broker_enabled:
        return None
    if _client is not None:
        return _client

    now = monotonic()
    if now < _retry_at:
        return None

    try:
        client = Redis.from_url(
            settings.redis_url,
            decode_responses=True,
            socket_timeout=1.0,
            socket_connect_timeout=1.0,
        )
        await client.ping()
    except Exception:
        _retry_at = now + _RETRY_COOLDOWN_SECONDS
        logger.warning(
            "Redis at %s is unreachable; falling back to in-process state",
            settings.redis_url,
            exc_info=True,
        )
        return None

    _client = client
    logger.info("Connected to Redis at %s", settings.redis_url)
    return _client


async def close_redis() -> None:
    global _client
    if _client is not None:
        await _client.aclose()
        _client = None


def reset_for_tests() -> None:
    """Forget the cached client and any failure cooldown."""
    global _client, _retry_at
    _client = None
    _retry_at = 0.0
