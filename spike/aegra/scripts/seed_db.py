"""Create the business schema and seed two applications with distinct secrets."""

from __future__ import annotations

import asyncio
import json
import os

import asyncpg

APPS = [
    {
        "app_id": "agriculture_app",
        "app_name": "农业业务系统",
        "jwt_config": {
            "algorithm": "HS256",
            "secret_ref": "env:AGRI_JWT_SECRET",
            "user_id_claim": "user_id",
            "require_exp": True,
            "leeway_seconds": 30,
        },
        "allowed_agents": ["*"],
        "allowed_workflows": ["*"],
    },
    {
        "app_id": "management_app",
        "app_name": "管理业务系统",
        "jwt_config": {
            "algorithm": "HS256",
            "secret_ref": "env:MGMT_JWT_SECRET",
            "user_id_claim": "user_id",
            "require_exp": True,
            "leeway_seconds": 30,
        },
        "allowed_agents": ["*"],
        "allowed_workflows": ["*"],
    },
]


async def main() -> None:
    dsn = os.environ.get("DATABASE_URL", "postgresql://aegra:aegra@127.0.0.1:5432/aegra")
    conn = await asyncpg.connect(dsn)
    try:
        await conn.execute("CREATE SCHEMA IF NOT EXISTS biz")
        await conn.execute(
            """
            CREATE TABLE IF NOT EXISTS biz.applications (
                app_id TEXT PRIMARY KEY,
                app_name TEXT NOT NULL,
                auth_type TEXT NOT NULL DEFAULT 'jwt',
                jwt_config JSONB NOT NULL,
                allowed_agents TEXT[] NOT NULL DEFAULT '{}',
                allowed_workflows TEXT[] NOT NULL DEFAULT '{}',
                enabled BOOLEAN NOT NULL DEFAULT TRUE,
                created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
                updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
            )
            """
        )
        for app in APPS:
            await conn.execute(
                """
                INSERT INTO biz.applications
                    (app_id, app_name, auth_type, jwt_config, allowed_agents, allowed_workflows, enabled)
                VALUES ($1, $2, 'jwt', $3::jsonb, $4, $5, TRUE)
                ON CONFLICT (app_id) DO UPDATE SET
                    app_name = EXCLUDED.app_name,
                    jwt_config = EXCLUDED.jwt_config,
                    allowed_agents = EXCLUDED.allowed_agents,
                    allowed_workflows = EXCLUDED.allowed_workflows,
                    enabled = TRUE,
                    updated_at = now()
                """,
                app["app_id"],
                app["app_name"],
                json.dumps(app["jwt_config"], ensure_ascii=False),
                app["allowed_agents"],
                app["allowed_workflows"],
            )
        rows = await conn.fetch(
            "SELECT app_id, allowed_agents, allowed_workflows FROM biz.applications ORDER BY app_id"
        )
        print(json.dumps([dict(row) for row in rows], ensure_ascii=False, indent=2))
    finally:
        await conn.close()


if __name__ == "__main__":
    asyncio.run(main())
