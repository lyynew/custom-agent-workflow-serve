"""Auth handler and authorization-handler tests."""

import os
import time
from types import SimpleNamespace

import jwt
import pytest
from langgraph_sdk import Auth

from app.auth.jwt import JwtConfig
from app.auth.manager import (
    auth,
    filter_thread_read,
    inject_thread_scope,
)
from app.auth.store import ApplicationConfig, application_store
from app.database.session import get_session_factory
from app.repositories.applications import ApplicationRepository


def _token(app_id: str, secret: str, ttl: int = 3600) -> str:
    now = int(time.time())
    payload = {"user_id": "10001", "app_id": app_id, "iat": now, "exp": now + ttl}
    return jwt.encode(payload, secret, algorithm="HS256")


def _headers(app_id: str, token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}", "X-App-Id": app_id}


async def _authenticate(headers: dict[str, str]) -> dict:
    handler = auth._authenticate_handler
    assert handler is not None
    return await handler(headers)


async def test_two_apps_get_namespaced_identities(seeded_apps):
    agri = await _authenticate(_headers("agriculture_app", _token("agriculture_app", os.environ["AGRI_JWT_SECRET"])))
    mgmt = await _authenticate(_headers("management_app", _token("management_app", os.environ["MGMT_JWT_SECRET"])))

    assert agri["identity"] == "agriculture_app:10001"
    assert mgmt["identity"] == "management_app:10001"
    assert agri["app_id"] == "agriculture_app"
    assert agri["user_id"] == "10001"


async def test_unknown_application_is_rejected(seeded_apps):
    token = _token("unknown_app", os.environ["AGRI_JWT_SECRET"])

    with pytest.raises(Auth.exceptions.HTTPException) as exc:
        await _authenticate(_headers("unknown_app", token))

    assert exc.value.status_code == 401


async def test_disabled_application_is_rejected(seeded_apps):
    config = ApplicationConfig(
        app_id="disabled_app",
        app_name="Disabled",
        jwt=JwtConfig(secret_ref="env:AGRI_JWT_SECRET"),
        allowed_agents=("*",),
        allowed_workflows=("*",),
        enabled=False,
    )
    async with get_session_factory()() as session:
        await ApplicationRepository(session).upsert(config)
        await session.commit()
    await application_store.reload()

    token = _token("disabled_app", os.environ["AGRI_JWT_SECRET"])
    with pytest.raises(Auth.exceptions.HTTPException) as exc:
        await _authenticate(_headers("disabled_app", token))

    assert exc.value.status_code == 403


async def test_missing_app_header_is_rejected(seeded_apps):
    with pytest.raises(Auth.exceptions.HTTPException) as exc:
        await _authenticate({"Authorization": "Bearer whatever"})

    assert exc.value.status_code == 401


async def test_missing_bearer_token_is_rejected(seeded_apps):
    with pytest.raises(Auth.exceptions.HTTPException) as exc:
        await _authenticate({"X-App-Id": "agriculture_app"})

    assert exc.value.status_code == 401


async def test_expired_token_is_rejected(seeded_apps):
    token = _token("agriculture_app", os.environ["AGRI_JWT_SECRET"], ttl=-60)

    with pytest.raises(Auth.exceptions.HTTPException) as exc:
        await _authenticate(_headers("agriculture_app", token))

    assert exc.value.status_code == 401


async def test_empty_allow_lists_are_preserved(seeded_apps):
    config = ApplicationConfig(
        app_id="empty_scope_app",
        app_name="Empty scope",
        jwt=JwtConfig(secret_ref="env:AGRI_JWT_SECRET"),
        allowed_agents=(),
        allowed_workflows=(),
    )
    async with get_session_factory()() as session:
        await ApplicationRepository(session).upsert(config)
        await session.commit()
    await application_store.reload()

    token = _token("empty_scope_app", os.environ["AGRI_JWT_SECRET"])
    result = await _authenticate(_headers("empty_scope_app", token))

    assert result["allowed_agents"] == []
    assert result["allowed_workflows"] == []


async def test_thread_scope_is_injected_into_metadata():
    ctx = SimpleNamespace(user=SimpleNamespace(app_id="agriculture_app", user_id="10001"))
    value = {"metadata": {"graph_id": "echo_agent"}}

    result = await inject_thread_scope(ctx, value)

    assert result["metadata"] == {
        "graph_id": "echo_agent",
        "app_id": "agriculture_app",
        "user_id": "10001",
    }


async def test_authorization_handler_denies_identity_without_scope():
    ctx = SimpleNamespace(user=SimpleNamespace())

    assert await filter_thread_read(ctx, {}) is False
