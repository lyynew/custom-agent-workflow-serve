"""Unit tests for per-application JWT verification."""

import os
import time

import jwt
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from pydantic import ValidationError

from app.auth.errors import AuthError, SecretResolutionError
from app.auth.jwt import JwtConfig, resolve_key, verify_token


def _config(**overrides) -> JwtConfig:
    values = {
        "algorithm": "HS256",
        "secret_ref": "env:AGRI_JWT_SECRET",
        "require_exp": True,
    }
    values.update(overrides)
    return JwtConfig(**values)


def _token(secret: str, payload: dict, algorithm: str = "HS256") -> str:
    return jwt.encode(payload, secret, algorithm=algorithm)


def _payload(**overrides) -> dict:
    now = int(time.time())
    payload = {"user_id": "10001", "app_id": "agriculture_app", "iat": now, "exp": now + 3600}
    payload.update(overrides)
    return payload


def test_valid_hs256_token():
    secret = os.environ["AGRI_JWT_SECRET"]
    token = _token(secret, _payload())

    assert verify_token(token, _config(), expected_app_id="agriculture_app") == "10001"


def test_expired_token_is_rejected():
    secret = os.environ["AGRI_JWT_SECRET"]
    token = _token(secret, _payload(exp=int(time.time()) - 60))

    with pytest.raises(AuthError) as exc:
        verify_token(token, _config(), expected_app_id="agriculture_app")

    assert exc.value.code == "token_expired"
    assert exc.value.status_code == 401


def test_leeway_allows_recent_expiry():
    secret = os.environ["AGRI_JWT_SECRET"]
    token = _token(secret, _payload(exp=int(time.time()) - 5))

    user_id = verify_token(
        token,
        _config(leeway_seconds=30),
        expected_app_id="agriculture_app",
    )
    assert user_id == "10001"


def test_wrong_signature_is_rejected():
    token = _token("not-the-real-secret-0123456789abcdef", _payload())

    with pytest.raises(AuthError) as exc:
        verify_token(token, _config(), expected_app_id="agriculture_app")

    assert exc.value.code == "invalid_token"


def test_alg_none_is_rejected():
    token = jwt.encode({"user_id": "10001", "exp": int(time.time()) + 3600}, "", algorithm="none")

    with pytest.raises(AuthError) as exc:
        verify_token(token, _config(), expected_app_id="agriculture_app")

    assert exc.value.code == "invalid_token"


def test_missing_exp_is_rejected_when_required():
    secret = os.environ["AGRI_JWT_SECRET"]
    token = _token(secret, {"user_id": "10001"})

    with pytest.raises(AuthError) as exc:
        verify_token(token, _config(require_exp=True), expected_app_id="agriculture_app")

    assert exc.value.code == "missing_claim"


def test_missing_exp_is_allowed_when_not_required():
    secret = os.environ["AGRI_JWT_SECRET"]
    token = _token(secret, {"user_id": "10001"})

    assert verify_token(
        token,
        _config(require_exp=False),
        expected_app_id="agriculture_app",
    ) == "10001"


def test_missing_user_claim_is_rejected():
    secret = os.environ["AGRI_JWT_SECRET"]
    token = _token(secret, _payload(user_id=None))

    with pytest.raises(AuthError) as exc:
        verify_token(token, _config(), expected_app_id="agriculture_app")

    assert exc.value.code == "invalid_claim"


def test_numeric_user_id_is_normalized_to_string():
    secret = os.environ["AGRI_JWT_SECRET"]
    token = _token(secret, _payload(user_id=10001))

    assert verify_token(token, _config(), expected_app_id="agriculture_app") == "10001"


def test_app_claim_mismatch_is_rejected():
    secret = os.environ["AGRI_JWT_SECRET"]
    token = _token(secret, _payload(app_id="management_app"))

    with pytest.raises(AuthError) as exc:
        verify_token(
            token,
            _config(app_id_claim="app_id"),
            expected_app_id="agriculture_app",
        )

    assert exc.value.code == "app_mismatch"


def test_rs256_token():
    private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    private_pem = private_key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    ).decode()
    public_pem = private_key.public_key().public_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PublicFormat.SubjectPublicKeyInfo,
    ).decode()

    os.environ["TEST_RSA_PUBLIC_KEY"] = public_pem
    config = JwtConfig(
        algorithm="RS256",
        public_key_ref="env:TEST_RSA_PUBLIC_KEY",
        require_exp=True,
    )
    token = jwt.encode(_payload(), private_pem, algorithm="RS256")

    assert verify_token(token, config, expected_app_id="agriculture_app") == "10001"


def test_algorithm_must_be_supported():
    with pytest.raises(ValidationError):
        JwtConfig(algorithm="none", secret_ref="env:AGRI_JWT_SECRET")


def test_hs_algorithm_requires_secret_ref():
    with pytest.raises(ValidationError):
        JwtConfig(algorithm="HS256")


def test_rs_algorithm_requires_public_key_ref():
    with pytest.raises(ValidationError):
        JwtConfig(algorithm="RS256", secret_ref="env:AGRI_JWT_SECRET")


def test_missing_secret_environment_variable():
    config = JwtConfig(secret_ref="env:DOES_NOT_EXIST", algorithm="HS256")

    with pytest.raises(SecretResolutionError):
        resolve_key(config)
