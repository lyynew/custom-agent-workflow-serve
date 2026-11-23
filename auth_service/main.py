"""HTTP 入口：`POST /auth/register`、`POST /auth/login`、`GET /auth/me`。

错误响应统一成主项目那套 `{"error": {"code", "message"}}` 信封，前端只写一份解析。

暴露的是工厂而不是模块级 `app`：签名密钥在 `AuthSettings.from_env()` 里强校验，
导入模块时就要密钥会让「只想 import 一下」的场景（比如单测）平白失败。
启动用 `uvicorn auth_service.main:create_app --factory`。
"""

import logging
import re
import sqlite3
import time
from collections import defaultdict, deque
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from typing import Annotated, Any

import aiosqlite
from fastapi import Depends, FastAPI, Header, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field, field_validator

from auth_service import db as store
from auth_service.config import AuthSettings
from auth_service.security import (
    MAX_PASSWORD_LENGTH,
    MIN_PASSWORD_LENGTH,
    TokenError,
    create_access_token,
    decode_token,
    hash_password,
    verify_password,
)

logger = logging.getLogger(__name__)

USERNAME_PATTERN = re.compile(r"^[\w.@-]{3,32}$", re.UNICODE)

#: 同一 IP + 用户名连续失败多少次后暂时拒绝。进程内计数，多副本部署要换 Redis。
MAX_LOGIN_FAILURES = 5
LOGIN_WINDOW_SECONDS = 300


class ApiError(Exception):
    def __init__(
        self,
        status_code: int,
        code: str,
        message: str,
        *,
        headers: dict[str, str] | None = None,
    ) -> None:
        self.status_code = status_code
        self.code = code
        self.message = message
        self.headers = headers or {}
        super().__init__(message)


class Credentials(BaseModel):
    username: str = Field(min_length=1, max_length=64)
    password: str = Field(min_length=1, max_length=MAX_PASSWORD_LENGTH)

    @field_validator("username")
    @classmethod
    def _strip(cls, value: str) -> str:
        return value.strip()


class RegisterRequest(Credentials):
    @field_validator("username")
    @classmethod
    def _check_username(cls, value: str) -> str:
        if not USERNAME_PATTERN.match(value):
            raise ValueError("用户名只能用字母、数字、下划线、点、@、中划线，长度 3-32")
        return value

    @field_validator("password")
    @classmethod
    def _check_password(cls, value: str) -> str:
        if len(value) < MIN_PASSWORD_LENGTH:
            raise ValueError(f"密码至少 {MIN_PASSWORD_LENGTH} 位")
        return value


class UserOut(BaseModel):
    user_id: str
    username: str


class RegisterResponse(BaseModel):
    user: UserOut
    created_at: str


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    expires_in: int
    expires_at: datetime
    app_id: str
    user: UserOut


class MeResponse(BaseModel):
    user_id: str
    username: str
    app_id: str
    issued_at: datetime | None = None
    expires_at: datetime | None = None


class PublicConfig(BaseModel):
    app_id: str
    token_ttl_minutes: int
    algorithm: str = "HS256"
    #: 调主项目时要带的租户头，写在这里省得前后端各记一份。
    app_id_header: str = "X-App-Id"


def create_app(settings: AuthSettings | None = None) -> FastAPI:
    cfg = settings or AuthSettings.from_env()
    failures: dict[str, deque[float]] = defaultdict(deque)

    @asynccontextmanager
    async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
        await store.init_database(cfg.database_path)
        logger.info(
            "auth service ready: app_id=%s ttl=%s min db=%s",
            cfg.app_id,
            cfg.token_ttl_minutes,
            cfg.database_path,
        )
        yield

    app = FastAPI(
        title="Auth Service",
        version="0.1.0",
        description="签发本项目可验证的 JWT：注册、登录、当前用户",
        lifespan=lifespan,
    )
    app.state.settings = cfg
    app.state.failures = failures

    if cfg.cors_origins:
        app.add_middleware(
            CORSMiddleware,
            allow_origins=list(cfg.cors_origins),
            allow_credentials=True,
            allow_methods=["*"],
            allow_headers=["*"],
        )

    @app.exception_handler(ApiError)
    async def _api_error(_request: Request, exc: ApiError) -> JSONResponse:
        return JSONResponse(
            status_code=exc.status_code,
            content={"error": {"code": exc.code, "message": exc.message}},
            headers=exc.headers or None,
        )

    async def get_db() -> AsyncIterator[aiosqlite.Connection]:
        async with store.connect(cfg.database_path) as connection:
            yield connection

    Db = Annotated[aiosqlite.Connection, Depends(get_db)]
    AuthHeader = Annotated[str | None, Header()]

    @app.get("/health", tags=["system"])
    async def health() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/auth/config", response_model=PublicConfig, tags=["auth"])
    async def public_config() -> PublicConfig:
        """前端启动时读一次，拿到 app_id 与 token 有效期，不必写死。"""
        return PublicConfig(app_id=cfg.app_id, token_ttl_minutes=cfg.token_ttl_minutes)

    @app.post("/auth/register", response_model=RegisterResponse, status_code=201, tags=["auth"])
    async def register(payload: RegisterRequest, db_conn: Db) -> RegisterResponse:
        password_hash = hash_password(payload.password)
        try:
            user = await store.create_user(db_conn, username=payload.username, password_hash=password_hash)
        except sqlite3.IntegrityError as exc:
            raise ApiError(409, "username_taken", "该用户名已被注册") from exc

        logger.info("registered user_id=%s", user["id"])
        return RegisterResponse(
            user=UserOut(user_id=user["id"], username=user["username"]),
            created_at=user["created_at"],
        )

    def attempt_key(request: Request, username: str) -> str:
        host = request.client.host if request.client else "unknown"
        return f"{host}:{username.lower()}"

    @app.post("/auth/login", response_model=TokenResponse, tags=["auth"])
    async def login(payload: Credentials, request: Request, db_conn: Db) -> TokenResponse:
        key = attempt_key(request, payload.username)
        window = failures[key]
        now = time.monotonic()
        while window and now - window[0] > LOGIN_WINDOW_SECONDS:
            window.popleft()
        if len(window) >= MAX_LOGIN_FAILURES:
            retry_after = max(1, int(LOGIN_WINDOW_SECONDS - (now - window[0])))
            raise ApiError(
                429,
                "too_many_attempts",
                "登录失败次数过多，请稍后再试",
                headers={"Retry-After": str(retry_after)},
            )

        user = await store.find_user_by_username(db_conn, payload.username)
        # 用户不存在与密码错误返回同一个错误，别帮人枚举账号。
        if user is None or not verify_password(payload.password, user["password_hash"]):
            window.append(time.monotonic())
            raise ApiError(401, "invalid_credentials", "用户名或密码不正确")

        failures.pop(key, None)
        token, expires_at = create_access_token(cfg, user_id=user["id"], username=user["username"])
        logger.info("login user_id=%s", user["id"])
        return TokenResponse(
            access_token=token,
            expires_in=cfg.token_ttl_minutes * 60,
            expires_at=expires_at,
            app_id=cfg.app_id,
            user=UserOut(user_id=user["id"], username=user["username"]),
        )

    @app.get("/auth/me", response_model=MeResponse, tags=["auth"])
    async def me(db_conn: Db, authorization: AuthHeader = None) -> MeResponse:
        scheme, _, token = (authorization or "").partition(" ")
        if scheme.lower() != "bearer" or not token.strip():
            raise ApiError(401, "missing_token", "缺少 Bearer token")

        try:
            payload = decode_token(cfg, token.strip())
        except TokenError as exc:
            raise ApiError(401, exc.code, exc.message) from exc

        user = await store.find_user_by_id(db_conn, str(payload["user_id"]))
        if user is None:
            # 用户被删但 token 还没过期：直接失效，别留 ghost 账号。
            raise ApiError(401, "unknown_user", "用户不存在")

        return MeResponse(
            user_id=user["id"],
            username=user["username"],
            app_id=str(payload.get("app_id") or cfg.app_id),
            issued_at=_from_timestamp(payload.get("iat")),
            expires_at=_from_timestamp(payload.get("exp")),
        )

    return app


def _from_timestamp(value: Any) -> datetime | None:
    try:
        return datetime.fromtimestamp(int(value), tz=UTC)
    except (TypeError, ValueError):
        return None
