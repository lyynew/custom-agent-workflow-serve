"""SQLite 存储：一个文件、一张表，够这个服务用。

不引 ORM：这里只有「按用户名查、按 id 查、插一行」三个动作，裸 SQL 更短也更好读。
连接按请求开、请求结束关——SQLite 的打开成本远低于一次 PBKDF2 校验。
"""

import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import aiosqlite

SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    id            TEXT PRIMARY KEY,
    -- COLLATE NOCASE：用户名大小写不敏感，Alice 和 alice 是同一个账号。
    username      TEXT NOT NULL UNIQUE COLLATE NOCASE,
    password_hash TEXT NOT NULL,
    created_at    TEXT NOT NULL
);
"""


def utc_now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


@asynccontextmanager
async def connect(path: Path) -> AsyncIterator[aiosqlite.Connection]:
    connection = await aiosqlite.connect(path)
    connection.row_factory = aiosqlite.Row
    try:
        yield connection
    finally:
        await connection.close()


async def init_database(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    async with connect(path) as db:
        # WAL 让并发读不挡写，多个 uvicorn worker 下也不至于互相锁死。
        await db.execute("PRAGMA journal_mode=WAL")
        await db.executescript(SCHEMA)
        await db.commit()


def _to_user(row: aiosqlite.Row) -> dict[str, Any]:
    return {
        "id": row["id"],
        "username": row["username"],
        "password_hash": row["password_hash"],
        "created_at": row["created_at"],
    }


async def create_user(db: aiosqlite.Connection, *, username: str, password_hash: str) -> dict[str, Any]:
    record = {
        "id": uuid.uuid4().hex,
        "username": username,
        "password_hash": password_hash,
        "created_at": utc_now(),
    }
    await db.execute(
        "INSERT INTO users (id, username, password_hash, created_at) VALUES (:id, :username, :password_hash, :created_at)",
        record,
    )
    await db.commit()
    return record


async def find_user_by_username(db: aiosqlite.Connection, username: str) -> dict[str, Any] | None:
    cursor = await db.execute("SELECT * FROM users WHERE username = ?", (username,))
    row = await cursor.fetchone()
    return _to_user(row) if row else None


async def find_user_by_id(db: aiosqlite.Connection, user_id: str) -> dict[str, Any] | None:
    cursor = await db.execute("SELECT * FROM users WHERE id = ?", (user_id,))
    row = await cursor.fetchone()
    return _to_user(row) if row else None
