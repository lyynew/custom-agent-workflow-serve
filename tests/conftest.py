"""Test fixtures.

The test database is created and migrated once per session. Each test runs on a
fresh engine so asyncpg connections never leak across pytest event loops.
"""

import asyncio
import os

os.environ["DATABASE_URL"] = os.environ.get(
    "TEST_DATABASE_URL",
    "postgresql+asyncpg://aegra:aegra@127.0.0.1:5432/ai_service_test",
)
os.environ.setdefault("AGRI_JWT_SECRET", "test-agri-secret-0123456789abcdef0123456789")
os.environ.setdefault("MGMT_JWT_SECRET", "test-mgmt-secret-0123456789abcdef0123456789")

# The suite seeds a `demo` model config, so it must not inherit whatever
# DEFAULT_MODEL_CONFIG (or a per-agent override) the developer's .env points at.
os.environ["DEFAULT_MODEL_CONFIG"] = "demo"
for _key in [k for k in os.environ if k.startswith("MODEL_CONFIG_")]:
    del os.environ[_key]

import asyncpg
import httpx
import pytest
import pytest_asyncio
from aegra_api.core.auth_deps import require_auth
from aegra_api.models.auth import User
from alembic import command
from alembic.config import Config
from sqlalchemy import text

from app.api.deps import get_runtime_adapter, get_task_launcher
from app.auth.context import AuthContext
from app.auth.jwt import JwtConfig
from app.auth.store import ApplicationConfig, application_store
from app.database.session import dispose_engine, get_session_factory
from app.main import app
from app.repositories.applications import ApplicationRepository
from tests.fakes import CollectingLauncher, FakeRuntime


def _split_database_url() -> tuple[str, str]:
    raw = os.environ["DATABASE_URL"].replace("+asyncpg", "")
    base, database = raw.rsplit("/", 1)
    return base + "/postgres", database.split("?", 1)[0]


async def _ensure_test_database() -> None:
    admin_url, database = _split_database_url()
    if not database.replace("_", "").isalnum():
        raise RuntimeError(f"Unsafe test database name: {database!r}")

    connection = await asyncpg.connect(admin_url)
    try:
        exists = await connection.fetchval("SELECT 1 FROM pg_database WHERE datname = $1", database)
        if not exists:
            await connection.execute(f'CREATE DATABASE "{database}"')
    finally:
        await connection.close()


@pytest.fixture(scope="session", autouse=True)
def prepare_database():
    asyncio.run(_ensure_test_database())

    config = Config("alembic.ini")
    config.attributes["sqlalchemy.url"] = os.environ["DATABASE_URL"]
    command.upgrade(config, "head")


async def _truncate_business_tables() -> None:
    async with get_session_factory()() as session:
        await session.execute(
            text(
                """
                TRUNCATE TABLE
                    biz.conversation_messages,
                    biz.conversations,
                    biz.workflow_runs,
                    biz.model_configs,
                    biz.applications
                RESTART IDENTITY CASCADE
                """
            )
        )
        await session.commit()


@pytest_asyncio.fixture(autouse=True)
async def clean_database(prepare_database):
    await _truncate_business_tables()
    application_store.clear()
    yield
    application_store.clear()
    await dispose_engine()


def _application(app_id: str, name: str, secret_ref: str, *, enabled: bool = True) -> ApplicationConfig:
    return ApplicationConfig(
        app_id=app_id,
        app_name=name,
        jwt=JwtConfig(secret_ref=secret_ref, algorithm="HS256", require_exp=True),
        allowed_agents=("*",),
        allowed_workflows=("*",),
        enabled=enabled,
    )


@pytest_asyncio.fixture
async def seeded_apps() -> list[ApplicationConfig]:
    configs = [
        _application("agriculture_app", "农业业务系统", "env:AGRI_JWT_SECRET"),
        _application("management_app", "管理业务系统", "env:MGMT_JWT_SECRET"),
    ]

    async with get_session_factory()() as session:
        repository = ApplicationRepository(session)
        for config in configs:
            await repository.upsert(config)
        await session.commit()

    await application_store.reload()
    return configs


@pytest_asyncio.fixture
async def session():
    async with get_session_factory()() as session:
        try:
            yield session
        finally:
            await session.rollback()


@pytest.fixture
def agri_context() -> AuthContext:
    return AuthContext(app_id="agriculture_app", user_id="10001")


@pytest.fixture
def mgmt_context() -> AuthContext:
    """Same user_id as agri_context, different application."""
    return AuthContext(app_id="management_app", user_id="10001")


@pytest.fixture
def other_user_context() -> AuthContext:
    return AuthContext(app_id="agriculture_app", user_id="20002")


@pytest_asyncio.fixture
async def asgi_client():
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(
        transport=transport,
        base_url="http://test",
        headers={"Authorization": "Bearer test-token"},
    ) as client:
        yield client


@pytest.fixture
def authenticated_as():
    def _set(user: User) -> None:
        async def _dependency() -> User:
            return user

        app.dependency_overrides[require_auth] = _dependency

    yield _set
    app.dependency_overrides.pop(require_auth, None)


@pytest.fixture
def fake_runtime():
    runtime = FakeRuntime()
    app.dependency_overrides[get_runtime_adapter] = lambda: runtime
    yield runtime
    app.dependency_overrides.pop(get_runtime_adapter, None)


@pytest.fixture
def workflow_launcher():
    """Collects background workflow coroutines so tests control when they run."""
    launcher = CollectingLauncher()
    app.dependency_overrides[get_task_launcher] = lambda: launcher
    try:
        yield launcher
    finally:
        app.dependency_overrides.pop(get_task_launcher, None)
        launcher.close()
