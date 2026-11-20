"""Agent listing respects the per-application allow list."""

from aegra_api.models.auth import User


def _user(allowed_agents: list[str]) -> User:
    return User(
        identity="agriculture_app:10001",
        app_id="agriculture_app",
        user_id="10001",
        allowed_agents=allowed_agents,
        allowed_workflows=["*"],
    )


async def test_wildcard_lists_registered_agents(
    seeded_apps, authenticated_as, asgi_client, fake_runtime
):
    authenticated_as(_user(["*"]))

    response = await asgi_client.get("/api/v1/agents")

    assert response.status_code == 200
    assert [agent["agent_id"] for agent in response.json()] == ["echo_agent"]


async def test_empty_allow_list_lists_nothing(
    seeded_apps, authenticated_as, asgi_client, fake_runtime
):
    authenticated_as(_user([]))

    response = await asgi_client.get("/api/v1/agents")

    assert response.status_code == 200
    assert response.json() == []


async def test_unregistered_allow_list_entry_is_not_exposed(
    seeded_apps, authenticated_as, asgi_client, fake_runtime
):
    authenticated_as(_user(["not_a_real_agent"]))

    response = await asgi_client.get("/api/v1/agents")

    assert response.status_code == 200
    assert response.json() == []
