"""Mint test tokens: two apps, same user_id, plus negative cases."""

from __future__ import annotations

import json
import os
import time

import jwt

AGRI = os.environ.get("AGRI_JWT_SECRET", "agri-dev-secret-change-me")
MGMT = os.environ.get("MGMT_JWT_SECRET", "mgmt-dev-secret-change-me")


def token(secret: str, app_id: str, user_id: str = "10001", ttl: int = 3600) -> str:
    now = int(time.time())
    payload = {"user_id": user_id, "app_id": app_id, "iat": now, "exp": now + ttl}
    return jwt.encode(payload, secret, algorithm="HS256")


def main() -> None:
    data = {
        "agri": token(AGRI, "agriculture_app"),
        "mgmt": token(MGMT, "management_app"),
        "expired": token(AGRI, "agriculture_app", ttl=-60),
        "wrong_sig": token("not-the-real-secret", "agriculture_app"),
        "alg_none": jwt.encode(
            {"user_id": "10001", "app_id": "agriculture_app", "exp": int(time.time()) + 3600},
            "",
            algorithm="none",
        ),
    }
    print(json.dumps(data, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
