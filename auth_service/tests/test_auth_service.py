"""认证服务测试：注册、登录、鉴权、限流，以及与主项目验签逻辑的对齐。

这一套不需要 PostgreSQL，全部跑在临时 SQLite 上，所以随时可以跑。
"""

from datetime import UTC, datetime, timedelta

import jwt
import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from auth_service import db as store
from auth_service.config import AuthSettings
from auth_service.main import create_app

SECRET = "test-agri-secret-0123456789abcdef0123456789"
APP_ID = "agriculture_app"


@pytest.fixture
def settings(tmp_path) -> AuthSettings:
    return AuthSettings(
        jwt_secret=SECRET,
        app_id=APP_ID,
        token_ttl_minutes=60,
        database_path=tmp_path / "auth.db",
        cors_origins=(),
    )


@pytest_asyncio.fixture
async def client(settings):
    # ASGITransport 不跑 lifespan，这里显式建表。
    await store.init_database(settings.database_path)
    app = create_app(settings)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://auth") as http:
        yield http


async def _register(client, username: str = "farmer", password: str = "wheat-2026") -> dict:
    response = await client.post("/auth/register", json={"username": username, "password": password})
    assert response.status_code == 201, response.text
    return response.json()


async def _login(client, username: str = "farmer", password: str = "wheat-2026") -> dict:
    response = await client.post("/auth/login", json={"username": username, "password": password})
    assert response.status_code == 200, response.text
    return response.json()


async def test_register_creates_a_user_without_leaking_secrets(client):
    body = await _register(client)

    assert body["user"]["username"] == "farmer"
    assert body["user"]["user_id"]
    assert "password" not in str(body)
    assert body["created_at"]


async def test_register_rejects_duplicate_names_case_insensitively(client):
    await _register(client, username="Farmer")

    response = await client.post("/auth/register", json={"username": "farmer", "password": "another-2026"})

    assert response.status_code == 409
    assert response.json()["error"]["code"] == "username_taken"


@pytest.mark.parametrize(
    ("username", "password"),
    [("ab", "wheat-2026"), ("has space", "wheat-2026"), ("farmer", "short")],
)
async def test_register_validates_input(client, username, password):
    response = await client.post("/auth/register", json={"username": username, "password": password})

    assert response.status_code == 422


async def test_login_returns_a_token_for_the_main_project(client):
    await _register(client)
    body = await _login(client)

    assert body["token_type"] == "bearer"
    assert body["app_id"] == APP_ID
    assert body["expires_in"] == 60 * 60

    payload = jwt.decode(body["access_token"], SECRET, algorithms=["HS256"])
    assert payload["user_id"] == body["user"]["user_id"]
    assert payload["sub"] == body["user"]["user_id"]
    assert payload["username"] == "farmer"
    assert payload["app_id"] == APP_ID
    assert payload["exp"] > payload["iat"]


async def test_login_failure_is_indistinguishable_from_unknown_user(client):
    await _register(client)

    wrong_password = await client.post("/auth/login", json={"username": "farmer", "password": "wrong-pass"})
    unknown_user = await client.post("/auth/login", json={"username": "nobody", "password": "wrong-pass"})

    assert wrong_password.status_code == unknown_user.status_code == 401
    assert wrong_password.json()["error"]["code"] == unknown_user.json()["error"]["code"]
    assert wrong_password.json()["error"]["message"] == unknown_user.json()["error"]["message"]


async def test_login_is_throttled_after_repeated_failures(client):
    await _register(client)

    for _ in range(5):
        await client.post("/auth/login", json={"username": "farmer", "password": "nope-nope"})

    response = await client.post("/auth/login", json={"username": "farmer", "password": "wheat-2026"})

    assert response.status_code == 429
    assert response.json()["error"]["code"] == "too_many_attempts"
    assert int(response.headers["Retry-After"]) > 0


async def test_me_returns_the_authenticated_identity(client):
    await _register(client)
    token = (await _login(client))["access_token"]

    response = await client.get("/auth/me", headers={"Authorization": f"Bearer {token}"})

    assert response.status_code == 200
    assert response.json()["username"] == "farmer"
    assert response.json()["app_id"] == APP_ID


@pytest.mark.parametrize(
    "headers",
    [{}, {"Authorization": "Bearer not-a-jwt"}, {"Authorization": "Basic abc"}, {"Authorization": "Bearer "}],
)
async def test_me_rejects_bad_tokens(client, headers):
    response = await client.get("/auth/me", headers=headers)

    assert response.status_code == 401


async def test_expired_token_is_rejected(client):
    await _register(client)
    user_id = (await _login(client))["user"]["user_id"]

    expired = jwt.encode(
        {
            "user_id": user_id,
            "sub": user_id,
            "username": "farmer",
            "app_id": APP_ID,
            "iat": int((datetime.now(UTC) - timedelta(hours=3)).timestamp()),
            "exp": int((datetime.now(UTC) - timedelta(hours=2)).timestamp()),
        },
        SECRET,
        algorithm="HS256",
    )

    response = await client.get("/auth/me", headers={"Authorization": f"Bearer {expired}"})

    assert response.status_code == 401
    assert response.json()["error"]["code"] == "token_expired"


async def test_public_config_exposes_the_tenant_header(client):
    response = await client.get("/auth/config")

    assert response.status_code == 200
    assert response.json() == {
        "app_id": APP_ID,
        "token_ttl_minutes": 60,
        "algorithm": "HS256",
        "app_id_header": "X-App-Id",
    }


def test_main_project_verifier_accepts_the_token(monkeypatch, settings):
    """最关键的一条：签出来的 token 必须能被主项目原样验证。"""
    from app.auth.jwt import JwtConfig, verify_token

    monkeypatch.setenv("AGRI_JWT_SECRET", SECRET)
    config = JwtConfig(algorithm="HS256", secret_ref="env:AGRI_JWT_SECRET", user_id_claim="user_id", require_exp=True)

    from auth_service.security import create_access_token

    token, _ = create_access_token(settings, user_id="u-1", username="farmer")

    assert verify_token(token, config, expected_app_id=APP_ID) == "u-1"


def test_main_project_verifier_rejects_a_token_from_another_secret(monkeypatch, settings):
    from app.auth.errors import AuthError
    from app.auth.jwt import JwtConfig, verify_token
    from auth_service.security import create_access_token

    monkeypatch.setenv("AGRI_JWT_SECRET", SECRET)
    config = JwtConfig(algorithm="HS256", secret_ref="env:AGRI_JWT_SECRET", user_id_claim="user_id")

    other = AuthSettings(
        jwt_secret="a-completely-different-secret-000000",
        app_id=APP_ID,
        token_ttl_minutes=60,
        database_path=settings.database_path,
        cors_origins=(),
    )
    token, _ = create_access_token(other, user_id="u-1", username="mallory")

    with pytest.raises(AuthError):
        verify_token(token, config, expected_app_id=APP_ID)


def test_main_project_verifier_pins_the_app_id_when_configured(monkeypatch, settings):
    """应用配了 app_id_claim 时，跨应用拿到的 token 必须被拒。"""
    from app.auth.errors import AuthError
    from app.auth.jwt import JwtConfig, verify_token
    from auth_service.security import create_access_token

    monkeypatch.setenv("AGRI_JWT_SECRET", SECRET)
    config = JwtConfig(
        algorithm="HS256",
        secret_ref="env:AGRI_JWT_SECRET",
        user_id_claim="user_id",
        app_id_claim="app_id",
    )

    token, _ = create_access_token(settings, user_id="u-1", username="farmer")

    with pytest.raises(AuthError):
        verify_token(token, config, expected_app_id="management_app")
