"""Small administrative CLI: seed and inspect applications and model configs."""

import argparse
import asyncio
import json
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

from sqlalchemy import delete

from app.auth.jwt import JwtConfig, resolve_key
from app.auth.secrets import resolve_secret
from app.auth.store import ApplicationConfig
from app.database.session import dispose_engine, get_session_factory
from app.models.audit_log import AuditLog
from app.repositories.applications import ApplicationRepository
from app.repositories.model_configs import ModelConfigRepository


def _load_configs(path: Path) -> list[ApplicationConfig]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, list):
        raise ValueError("Seed file must contain a JSON array")

    configs: list[ApplicationConfig] = []
    for item in payload:
        configs.append(
            ApplicationConfig(
                app_id=item["app_id"],
                app_name=item["app_name"],
                auth_type=item.get("auth_type", "jwt"),
                jwt=JwtConfig.model_validate(item["jwt_config"]),
                allowed_agents=tuple(item.get("allowed_agents", ())),
                allowed_workflows=tuple(item.get("allowed_workflows", ())),
                enabled=bool(item.get("enabled", True)),
                rate_limit_per_minute=item.get("rate_limit_per_minute"),
                daily_request_quota=item.get("daily_request_quota"),
                max_concurrent_workflow_runs=item.get("max_concurrent_workflow_runs"),
            )
        )
    return configs


async def _seed(configs: list[ApplicationConfig]) -> None:
    for config in configs:
        resolve_key(config.jwt)  # fail before writing a broken configuration

    async with get_session_factory()() as session:
        repository = ApplicationRepository(session)
        for config in configs:
            await repository.upsert(config)
        await session.commit()

    for config in configs:
        print(f"seeded {config.app_id} ({config.app_name})")


async def _list() -> None:
    async with get_session_factory()() as session:
        rows = await ApplicationRepository(session).list_all()

    print(
        json.dumps(
            [
                {
                    "app_id": row.app_id,
                    "app_name": row.app_name,
                    "enabled": row.enabled,
                    "algorithm": row.jwt_config.get("algorithm"),
                    "key_ref": row.jwt_config.get("secret_ref") or row.jwt_config.get("public_key_ref"),
                    "allowed_agents": row.allowed_agents,
                    "allowed_workflows": row.allowed_workflows,
                    "rate_limit_per_minute": row.rate_limit_per_minute,
                    "daily_request_quota": row.daily_request_quota,
                    "max_concurrent_workflow_runs": row.max_concurrent_workflow_runs,
                }
                for row in rows
            ],
            ensure_ascii=False,
            indent=2,
        )
    )


async def _seed_models(path: Path) -> None:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, list):
        raise ValueError("Seed file must contain a JSON array")

    async with get_session_factory()() as session:
        repository = ModelConfigRepository(session)
        for item in payload:
            await repository.upsert(item)
        await session.commit()

    for item in payload:
        print(f"seeded model {item['name']} ({item['provider']}/{item['model_name']})")


async def _list_models() -> None:
    async with get_session_factory()() as session:
        rows = await ModelConfigRepository(session).list_all()

    print(
        json.dumps(
            [
                {
                    "name": row.name,
                    "provider": row.provider,
                    "model_name": row.model_name,
                    "base_url": row.base_url,
                    # A reference, never a secret.
                    "key_ref": row.api_key_encrypted,
                    "enabled": row.enabled,
                }
                for row in rows
            ],
            ensure_ascii=False,
            indent=2,
        )
    )


async def _prune_audit(days: int, app_id: str | None) -> None:
    cutoff = datetime.now(UTC) - timedelta(days=days)
    statement = delete(AuditLog).where(AuditLog.created_at < cutoff)
    if app_id:
        statement = statement.where(AuditLog.app_id == app_id)

    async with get_session_factory()() as session:
        result = await session.execute(statement)
        await session.commit()

    scope = f"app {app_id}" if app_id else "all applications"
    print(f"pruned {result.rowcount or 0} audit rows older than {days} days for {scope}")


async def _validate() -> None:
    async with get_session_factory()() as session:
        rows = await ApplicationRepository(session).list_all()
        models = await ModelConfigRepository(session).list_all()

    failures = 0
    for row in rows:
        config = ApplicationConfig.from_row(row)
        try:
            resolve_key(config.jwt)
        except Exception as exc:  # noqa: BLE001 - report every broken app
            failures += 1
            print(f"FAIL {config.app_id}: {exc}", file=sys.stderr)
        else:
            print(f"OK   {config.app_id}")

    for model in models:
        if not model.enabled or not model.api_key_encrypted:
            continue
        try:
            resolve_secret(model.api_key_encrypted)
        except Exception as exc:  # noqa: BLE001 - report every broken model config
            failures += 1
            print(f"FAIL model {model.name}: {exc}", file=sys.stderr)
        else:
            print(f"OK   model {model.name}")

    if failures:
        raise SystemExit(1)


async def _run(args: argparse.Namespace) -> None:
    try:
        if args.command == "seed-apps":
            await _seed(_load_configs(Path(args.file)))
        elif args.command == "list-apps":
            await _list()
        elif args.command == "seed-models":
            await _seed_models(Path(args.file))
        elif args.command == "list-models":
            await _list_models()
        elif args.command == "prune-audit":
            await _prune_audit(args.days, args.app_id)
        elif args.command == "validate":
            await _validate()
    finally:
        await dispose_engine()


def main() -> None:
    parser = argparse.ArgumentParser(prog="app.cli")
    subparsers = parser.add_subparsers(dest="command", required=True)

    seed = subparsers.add_parser("seed-apps", help="Insert or update applications from a JSON file")
    seed.add_argument("--file", default="seed/applications.json")

    subparsers.add_parser("list-apps", help="List registered applications (no secrets)")

    seed_models = subparsers.add_parser("seed-models", help="Insert or update model configs from a JSON file")
    seed_models.add_argument("--file", default="seed/models.json")

    subparsers.add_parser("list-models", help="List model configs (key references only)")

    prune = subparsers.add_parser("prune-audit", help="Delete audit rows older than N days")
    prune.add_argument("--days", type=int, default=30)
    prune.add_argument("--app-id", default=None, help="只清理某个应用；默认全部")

    subparsers.add_parser("validate", help="Validate every secret_ref, key reference and model config")

    asyncio.run(_run(parser.parse_args()))


if __name__ == "__main__":
    main()
