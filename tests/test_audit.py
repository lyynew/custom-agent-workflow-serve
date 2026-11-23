"""审计日志：记录、租户隔离、筛选、概览。"""

import asyncio
from datetime import UTC, datetime, timedelta
from urllib.parse import quote

from aegra_api.models.auth import User
from sqlalchemy import select

from app.database.session import get_session_factory
from app.models.audit_log import AuditLog


def _user(app_id: str = "agriculture_app", user_id: str = "10001") -> User:
    return User(
        identity=f"{app_id}:{user_id}",
        app_id=app_id,
        user_id=user_id,
        allowed_agents=["*"],
        allowed_workflows=["*"],
    )


async def _rows(app_id: str = "agriculture_app") -> list[AuditLog]:
    async with get_session_factory()() as session:
        return list(
            (
                await session.scalars(
                    select(AuditLog).where(AuditLog.app_id == app_id).order_by(AuditLog.created_at)
                )
            ).all()
        )


async def _wait_for_rows(count: int, app_id: str = "agriculture_app", timeout: float = 3.0) -> list[AuditLog]:
    """审计是后台任务，轮询等它落库，别用固定 sleep 赌时序。"""
    deadline = asyncio.get_event_loop().time() + timeout
    while True:
        rows = await _rows(app_id)
        if len(rows) >= count or asyncio.get_event_loop().time() > deadline:
            return rows
        await asyncio.sleep(0.05)


async def test_authenticated_request_is_recorded(
    seeded_apps, authenticated_as, asgi_client, fake_runtime
):
    authenticated_as(_user())

    response = await asgi_client.get("/api/v1/agents")
    assert response.status_code == 200

    rows = await _wait_for_rows(1)

    assert len(rows) == 1
    row = rows[0]
    assert row.app_id == "agriculture_app"
    assert row.user_id == "10001"
    assert row.method == "GET"
    assert row.path == "/api/v1/agents"
    assert row.status_code == 200
    assert row.duration_ms >= 0


async def test_unauthenticated_requests_are_not_recorded(
    seeded_apps, authenticated_as, asgi_client, fake_runtime
):
    response = await asgi_client.get("/api/v1/agents")
    assert response.status_code == 401

    # 给后台任务一点时间，确认它确实什么都没写。
    await asyncio.sleep(0.2)
    assert await _rows() == []


async def test_logs_are_isolated_per_application(seeded_apps, authenticated_as, asgi_client, fake_runtime):
    authenticated_as(_user("management_app", "20002"))
    assert (await asgi_client.get("/api/v1/whoami")).status_code == 200
    await _wait_for_rows(1, app_id="management_app")

    authenticated_as(_user())
    assert (await asgi_client.get("/api/v1/agents")).status_code == 200
    await _wait_for_rows(1)

    response = await asgi_client.get("/api/v1/audit/logs")

    assert response.status_code == 200
    items = response.json()["items"]
    assert items, "本应用的请求应该能看到"
    assert {item["app_id"] for item in items} == {"agriculture_app"}


async def test_filters_by_status_method_and_path(seeded_apps, authenticated_as, asgi_client, fake_runtime):
    authenticated_as(_user())

    await asgi_client.get("/api/v1/agents")
    await asgi_client.get("/api/v1/whoami")
    await with_missing_agent(authenticated_as, asgi_client)
    await _wait_for_rows(3)

    not_found = await asgi_client.get("/api/v1/audit/logs?status=4xx")
    assert not_found.status_code == 200
    assert {item["status_code"] for item in not_found.json()["items"]} == {404}

    by_method = await asgi_client.get("/api/v1/audit/logs?method=GET&path=/api/v1/agents")
    paths = [item["path"] for item in by_method.json()["items"]]
    assert paths and all(item.startswith("/api/v1/agents") for item in paths)


async def with_missing_agent(authenticated_as, asgi_client) -> None:
    response = await asgi_client.post("/api/v1/agents/not_a_real_agent/chat", json={"message": "hi"})
    assert response.status_code == 404


async def test_summary_counts_errors_and_users(seeded_apps, authenticated_as, asgi_client, fake_runtime):
    authenticated_as(_user())
    await asgi_client.get("/api/v1/agents")
    await with_missing_agent(authenticated_as, asgi_client)
    await _wait_for_rows(2)

    response = await asgi_client.get("/api/v1/audit/summary?hours=1")

    assert response.status_code == 200
    body = response.json()
    assert body["window_hours"] == 1
    assert body["requests"] >= 2
    assert body["client_errors"] >= 1
    assert body["users"] == 1
    assert body["error_rate"] > 0


async def test_pagination_returns_a_cursor(seeded_apps, authenticated_as, asgi_client, fake_runtime):
    authenticated_as(_user())
    for _ in range(3):
        await asgi_client.get("/api/v1/agents")
    await _wait_for_rows(3)

    first = await asgi_client.get("/api/v1/audit/logs?limit=2")

    assert first.status_code == 200
    body = first.json()
    assert len(body["items"]) == 2
    assert body["next_before"]

    cursor = quote(str(body["next_before"]), safe="")
    second = await asgi_client.get(f"/api/v1/audit/logs?limit=2&before={cursor}")
    assert second.status_code == 200
    assert {item["id"] for item in second.json()["items"]}.isdisjoint(
        {item["id"] for item in body["items"]}
    )


async def test_summary_window_excludes_old_rows(seeded_apps, authenticated_as, asgi_client, fake_runtime):
    authenticated_as(_user())
    await asgi_client.get("/api/v1/agents")
    await _wait_for_rows(1)

    async with get_session_factory()() as session:
        row = (await session.scalars(select(AuditLog))).first()
        row.created_at = datetime.now(UTC) - timedelta(days=3)
        await session.commit()

    response = await asgi_client.get("/api/v1/audit/summary?hours=24")

    assert response.json()["requests"] == 0
