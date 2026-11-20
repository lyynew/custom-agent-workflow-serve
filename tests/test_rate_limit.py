"""Per-application rate limiting and daily quota."""

import pytest
from aegra_api.models.auth import User

from app.services.rate_limit import RateLimiter, rate_limiter


def _user(
    *,
    app_id: str = "agriculture_app",
    user_id: str = "10001",
    rate_limit_per_minute: int | None = None,
    daily_request_quota: int | None = None,
) -> User:
    return User(
        identity=f"{app_id}:{user_id}",
        app_id=app_id,
        user_id=user_id,
        allowed_agents=["*"],
        allowed_workflows=["*"],
        rate_limit_per_minute=rate_limit_per_minute,
        daily_request_quota=daily_request_quota,
    )


@pytest.fixture(autouse=True)
def reset_counters():
    rate_limiter.reset()
    yield
    rate_limiter.reset()


async def test_application_without_limits_is_never_throttled(
    seeded_apps, authenticated_as, asgi_client
):
    authenticated_as(_user())

    for _ in range(5):
        response = await asgi_client.get("/api/v1/whoami")

    assert response.status_code == 200


async def test_rate_limit_rejects_after_the_configured_number(
    seeded_apps, authenticated_as, asgi_client
):
    authenticated_as(_user(rate_limit_per_minute=2))

    first = await asgi_client.get("/api/v1/whoami")
    second = await asgi_client.get("/api/v1/whoami")
    third = await asgi_client.get("/api/v1/whoami")

    assert first.status_code == 200
    assert second.status_code == 200
    assert third.status_code == 429
    assert third.json()["error"]["code"] == "rate_limited"
    assert third.headers["Retry-After"].isdigit()
    assert third.headers["X-RateLimit-Remaining"] == "0"
    assert third.headers["X-RateLimit-Limit"] == "2"


async def test_daily_quota_rejects_after_the_configured_number(
    seeded_apps, authenticated_as, asgi_client
):
    authenticated_as(_user(daily_request_quota=1))

    first = await asgi_client.get("/api/v1/whoami")
    second = await asgi_client.get("/api/v1/whoami")

    assert first.status_code == 200
    assert second.status_code == 429
    assert second.json()["error"]["code"] == "daily_quota_exceeded"


async def test_limits_apply_to_business_routes_not_only_system(seeded_apps, authenticated_as, asgi_client):
    authenticated_as(_user(rate_limit_per_minute=1))

    await asgi_client.get("/api/v1/agents")  # consumes the single token
    response = await asgi_client.get("/api/v1/workflows")

    assert response.status_code == 429


async def test_counters_are_isolated_per_application():
    limiter = RateLimiter(namespace="test")

    assert (await limiter.check("app_a", per_minute=1)).allowed
    assert not (await limiter.check("app_a", per_minute=1)).allowed
    # A different application starts from zero.
    assert (await limiter.check("app_b", per_minute=1)).allowed


async def test_counters_fall_back_to_process_memory_without_redis():
    limiter = RateLimiter(namespace="test-fallback")

    first = await limiter.check("app", per_minute=2)
    second = await limiter.check("app", per_minute=2)
    third = await limiter.check("app", per_minute=2)

    assert first.remaining == 1
    assert second.remaining == 0
    assert not third.allowed
