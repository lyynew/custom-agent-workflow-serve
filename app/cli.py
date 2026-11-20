"""Small administrative CLI: seed and inspect application registrations."""

import argparse
import asyncio
import json
import sys
from pathlib import Path

from app.auth.jwt import JwtConfig, resolve_key
from app.auth.store import ApplicationConfig
from app.database.session import dispose_engine, get_session_factory
from app.repositories.applications import ApplicationRepository


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
                }
                for row in rows
            ],
            ensure_ascii=False,
            indent=2,
        )
    )


async def _validate() -> None:
    async with get_session_factory()() as session:
        rows = await ApplicationRepository(session).list_all()

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

    if failures:
        raise SystemExit(1)


async def _run(args: argparse.Namespace) -> None:
    try:
        if args.command == "seed-apps":
            await _seed(_load_configs(Path(args.file)))
        elif args.command == "list-apps":
            await _list()
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
    subparsers.add_parser("validate", help="Validate every secret_ref and key configuration")

    asyncio.run(_run(parser.parse_args()))


if __name__ == "__main__":
    main()
