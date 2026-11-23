"""认证服务配置：密钥与主项目共用一份，避免签验两端漂移。"""

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

# 先读仓库根目录的 .env，再读服务自己的 .env（存在才读，后者覆盖前者）。
_REPO_ROOT = Path(__file__).resolve().parent.parent
load_dotenv(_REPO_ROOT / ".env")
load_dotenv(Path(__file__).resolve().parent / ".env", override=True)

DEFAULT_APP_ID = "agriculture_app"
DEFAULT_DB_PATH = Path(__file__).resolve().parent / "auth.db"


class AuthConfigError(RuntimeError):
    """缺少必需配置；启动时就该失败，而不是签出一批没人认的 token。"""


def _int_env(name: str, default: int) -> int:
    raw = os.environ.get(name)
    if not raw:
        return default
    try:
        value = int(raw)
    except ValueError as exc:
        raise AuthConfigError(f"{name} 必须是整数，当前是 {raw!r}") from exc
    if value <= 0:
        raise AuthConfigError(f"{name} 必须大于 0，当前是 {value}")
    return value


@dataclass(frozen=True)
class AuthSettings:
    jwt_secret: str
    app_id: str
    token_ttl_minutes: int
    database_path: Path
    cors_origins: tuple[str, ...]

    @classmethod
    def from_env(cls) -> "AuthSettings":
        # 与 seed/applications.example.json 里 agriculture_app 的 secret_ref 同一个变量。
        secret = os.environ.get("AGRI_JWT_SECRET") or os.environ.get("AUTH_JWT_SECRET")
        if not secret:
            raise AuthConfigError(
                "缺少 AGRI_JWT_SECRET：它必须与主项目 applications.jwt_config.secret_ref 指向的变量一致"
            )

        origins = os.environ.get(
            "AUTH_CORS_ORIGINS",
            "http://localhost:5173,http://127.0.0.1:5173",
        )
        return cls(
            jwt_secret=secret,
            app_id=os.environ.get("AUTH_APP_ID", DEFAULT_APP_ID),
            token_ttl_minutes=_int_env("AUTH_TOKEN_TTL_MINUTES", 120),
            database_path=Path(os.environ.get("AUTH_DB_PATH", str(DEFAULT_DB_PATH))),
            cors_origins=tuple(origin.strip() for origin in origins.split(",") if origin.strip()),
        )
