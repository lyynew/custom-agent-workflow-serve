"""密码哈希与 JWT 签发。

密码用 PBKDF2-HMAC-SHA256 + 随机盐，存 `pbkdf2_sha256$<iterations>$<salt>$<hash>`。
不引 bcrypt/argon2 是有意的：这两个包都要编译或带原生依赖，而 PBKDF2 在标准库里，
迭代次数够高就够了（这里 24 万次）。

token 用 HS256，claims：`sub`/`user_id`/`username`/`app_id`/`iat`/`exp`/`jti`。
`user_id` 是主项目 `user_id_claim` 的默认值，缺了它会被判 401。
"""

import base64
import hashlib
import hmac
import secrets
from datetime import UTC, datetime, timedelta
from typing import Any

import jwt

from auth_service.config import AuthSettings

PBKDF2_ITERATIONS = 240_000
SALT_BYTES = 16
ALGORITHM = "HS256"
MIN_PASSWORD_LENGTH = 8
MAX_PASSWORD_LENGTH = 128


class TokenError(Exception):
    """token 无效或过期。"""

    def __init__(self, code: str, message: str) -> None:
        self.code = code
        self.message = message
        super().__init__(message)


def hash_password(password: str) -> str:
    salt = secrets.token_bytes(SALT_BYTES)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, PBKDF2_ITERATIONS)
    return "$".join(
        [
            "pbkdf2_sha256",
            str(PBKDF2_ITERATIONS),
            base64.b64encode(salt).decode(),
            base64.b64encode(digest).decode(),
        ]
    )


def verify_password(password: str, stored: str) -> bool:
    try:
        scheme, iterations_raw, salt_raw, digest_raw = stored.split("$")
        if scheme != "pbkdf2_sha256":
            return False
        iterations = int(iterations_raw)
        salt = base64.b64decode(salt_raw)
        expected = base64.b64decode(digest_raw)
    except (ValueError, TypeError):
        # 存坏的哈希一律当验证失败，不抛给调用方。
        return False

    candidate = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, iterations)
    return hmac.compare_digest(candidate, expected)


def create_access_token(
    settings: AuthSettings,
    *,
    user_id: str,
    username: str,
    now: datetime | None = None,
) -> tuple[str, datetime]:
    issued_at = now or datetime.now(UTC)
    expires_at = issued_at + timedelta(minutes=settings.token_ttl_minutes)

    payload: dict[str, Any] = {
        "sub": user_id,
        # 主项目 `JwtConfig.user_id_claim` 默认读这个字段。
        "user_id": user_id,
        "username": username,
        "app_id": settings.app_id,
        "iat": int(issued_at.timestamp()),
        "exp": int(expires_at.timestamp()),
        "jti": secrets.token_hex(8),
    }
    token = jwt.encode(payload, settings.jwt_secret, algorithm=ALGORITHM)
    return token, expires_at


def decode_token(settings: AuthSettings, token: str) -> dict[str, Any]:
    try:
        payload = jwt.decode(
            token,
            settings.jwt_secret,
            algorithms=[ALGORITHM],
            options={"require": ["exp", "iat"]},
        )
    except jwt.ExpiredSignatureError as exc:
        raise TokenError("token_expired", "Token has expired") from exc
    except jwt.InvalidTokenError as exc:
        raise TokenError("invalid_token", "Token verification failed") from exc

    if not str(payload.get("user_id") or "").strip():
        raise TokenError("invalid_claim", "Token is missing user_id")
    return payload
