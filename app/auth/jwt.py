"""JWT verification with per-application keys and algorithms."""

from datetime import timedelta
from typing import Any

import jwt
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.auth.errors import AuthError
from app.auth.secrets import resolve_secret

SUPPORTED_ALGORITHMS = frozenset({"HS256", "HS384", "HS512", "RS256", "RS384", "RS512"})


class JwtConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    algorithm: str = "HS256"
    secret_ref: str | None = None
    public_key_ref: str | None = None
    user_id_claim: str = Field(default="user_id", min_length=1)
    app_id_claim: str | None = None
    require_exp: bool = True
    issuer: str | None = None
    audience: str | None = None
    leeway_seconds: int = Field(default=30, ge=0)

    @field_validator("algorithm")
    @classmethod
    def validate_algorithm(cls, value: str) -> str:
        normalized = value.strip().upper()
        if normalized not in SUPPORTED_ALGORITHMS:
            raise ValueError(
                f"Unsupported JWT algorithm '{value}'. "
                f"Supported: {', '.join(sorted(SUPPORTED_ALGORITHMS))}"
            )
        return normalized

    @model_validator(mode="after")
    def validate_key_reference(self) -> "JwtConfig":
        if self.algorithm.startswith("HS") and not self.secret_ref:
            raise ValueError("HS* algorithms require secret_ref")
        if self.algorithm.startswith("RS") and not self.public_key_ref:
            raise ValueError("RS* algorithms require public_key_ref")
        return self


def resolve_key(config: JwtConfig) -> str:
    if config.algorithm.startswith("HS"):
        assert config.secret_ref is not None
        return resolve_secret(config.secret_ref)
    assert config.public_key_ref is not None
    return resolve_secret(config.public_key_ref)


def _extract_user_id(payload: dict[str, Any], config: JwtConfig) -> str:
    value = payload.get(config.user_id_claim)

    # bool is a subclass of int; reject it explicitly.
    if value is None or isinstance(value, bool):
        raise AuthError("invalid_claim", f"Missing or invalid claim: {config.user_id_claim}")

    if isinstance(value, int):
        return str(value)

    if isinstance(value, str) and value.strip():
        return value.strip()

    raise AuthError("invalid_claim", f"Missing or invalid claim: {config.user_id_claim}")


def verify_token(
    token: str,
    config: JwtConfig,
    *,
    expected_app_id: str,
    key: str | None = None,
) -> str:
    """Verify a token for one application and return its `user_id`.

    The algorithm is pinned to the application configuration, which rejects
    `alg=none` and algorithm-confusion attacks.
    """
    verification_key = key if key is not None else resolve_key(config)

    options = {
        "require": ["exp"] if config.require_exp else [],
        "verify_exp": True,
        "verify_aud": config.audience is not None,
        "verify_iss": config.issuer is not None,
    }

    try:
        payload = jwt.decode(
            token,
            verification_key,
            algorithms=[config.algorithm],
            options=options,
            audience=config.audience,
            issuer=config.issuer,
            leeway=timedelta(seconds=config.leeway_seconds),
        )
    except jwt.ExpiredSignatureError as exc:
        raise AuthError("token_expired", "Token has expired") from exc
    except jwt.MissingRequiredClaimError as exc:
        raise AuthError("missing_claim", "Token is missing a required claim") from exc
    except jwt.InvalidTokenError as exc:
        # Do not leak signature/issuer/audience details to the caller.
        raise AuthError("invalid_token", "Token verification failed") from exc

    user_id = _extract_user_id(payload, config)

    if config.app_id_claim:
        token_app_id = payload.get(config.app_id_claim)
        if token_app_id != expected_app_id:
            raise AuthError("app_mismatch", "Token does not belong to this application")

    return user_id
