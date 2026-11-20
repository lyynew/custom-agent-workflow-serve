"""Quota dependency applied to every business route.

The per-application limits travel on the authenticated principal (populated by
the auth handler), so enforcing them costs one counter increment and no extra
database round-trip.
"""

from aegra_api.core.auth_deps import require_auth
from aegra_api.models.auth import User
from fastapi import Depends

from app.api.errors import APIError
from app.services.rate_limit import rate_limiter


async def enforce_quota(user: User = Depends(require_auth)) -> None:
    app_id = getattr(user, "app_id", None)
    if not app_id:
        return

    per_minute = getattr(user, "rate_limit_per_minute", None)
    per_day = getattr(user, "daily_request_quota", None)
    if not per_minute and not per_day:
        return

    decision = await rate_limiter.check(app_id, per_minute=per_minute, per_day=per_day)
    if decision.allowed:
        return

    headers = {
        "X-RateLimit-Limit": str(decision.limit or ""),
        "X-RateLimit-Remaining": "0",
        "X-RateLimit-Reset": str(decision.retry_after or ""),
        "Retry-After": str(decision.retry_after or 1),
    }

    if decision.scope == "quota":
        raise APIError(
            429,
            "daily_quota_exceeded",
            "Daily request quota exhausted for this application",
            headers=headers,
        )

    raise APIError(429, "rate_limited", "Too many requests", headers=headers)
