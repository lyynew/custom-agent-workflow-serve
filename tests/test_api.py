"""HTTP-level smoke tests with the auth dependency overridden."""

from aegra_api.models.auth import User


def _user(app_id: str = "agriculture_app", user_id: str = "10001") -> User:
    return User(
        identity=f"{app_id}:{user_id}",
        app_id=app_id,
        user_id=user_id,
        allowed_agents=["*"],
        allowed_workflows=["*"],
    )


async def test_whoami_returns_namespaced_identity(authenticated_as, asgi_client):
    authenticated_as(_user())

    response = await asgi_client.get("/api/v1/whoami")

    assert response.status_code == 200
    assert response.json()["identity"] == "agriculture_app:10001"
    assert response.json()["app_id"] == "agriculture_app"


async def test_apps_me_returns_application_config(seeded_apps, authenticated_as, asgi_client):
    authenticated_as(_user())

    response = await asgi_client.get("/api/v1/apps/me")

    assert response.status_code == 200
    assert response.json()["app_name"] == "农业业务系统"
    assert response.json()["allowed_agents"] == ["*"]


async def test_unknown_application_uses_error_envelope(seeded_apps, authenticated_as, asgi_client):
    authenticated_as(_user("ghost_app", "1"))

    response = await asgi_client.get("/api/v1/apps/me")

    assert response.status_code == 404
    body = response.json()
    assert body["error"]["code"] == "application_not_found"
    assert "ghost_app" not in body["error"]["message"]
