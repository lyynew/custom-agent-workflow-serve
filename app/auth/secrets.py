"""Resolve `secret_ref` values without ever storing secrets in the database."""

import os

from app.auth.errors import SecretResolutionError

ENV_PREFIX = "env:"


def resolve_secret(secret_ref: str) -> str:
    """Resolve ``env:NAME`` to its value.

    Only environment references are supported in v1. This keeps secrets out of
    the database, API responses, and logs.
    """
    if not secret_ref.startswith(ENV_PREFIX):
        raise SecretResolutionError(
            f"Unsupported secret_ref '{secret_ref}'. Expected format: env:VARIABLE_NAME"
        )

    name = secret_ref[len(ENV_PREFIX) :].strip()
    if not name:
        raise SecretResolutionError("secret_ref is missing an environment variable name")

    value = os.environ.get(name)
    if not value:
        raise SecretResolutionError(f"Environment variable '{name}' is not set")
    return value
