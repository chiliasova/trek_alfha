import json

import asyncpg
from pgvector.asyncpg import register_vector

from app.config import settings

_pool: asyncpg.Pool | None = None


async def _init_conn(conn: asyncpg.Connection) -> None:
    await register_vector(conn)
    await conn.set_type_codec("jsonb", encoder=json.dumps, decoder=json.loads, schema="pg_catalog")


async def get_pool() -> asyncpg.Pool:
    global _pool
    if _pool is None:
        _pool = await asyncpg.create_pool(settings.database_url, init=_init_conn, min_size=1, max_size=5)
    return _pool


async def close_pool() -> None:
    global _pool
    if _pool is not None:
        await _pool.close()
        _pool = None


async def touch_user(user_id: int, username: str | None, first_name: str | None) -> None:
    pool = await get_pool()
    await pool.execute(
        """
        INSERT INTO users (id, username, first_name) VALUES ($1, $2, $3)
        ON CONFLICT (id) DO UPDATE SET username = EXCLUDED.username,
            first_name = EXCLUDED.first_name, last_seen_at = now()
        """,
        user_id, username, first_name,
    )


async def log_event(user_id: int | None, type_: str, payload: dict | None = None) -> None:
    pool = await get_pool()
    await pool.execute(
        "INSERT INTO events (user_id, type, payload) VALUES ($1, $2, $3)",
        user_id, type_, payload or {},
    )


async def save_message(user_id: int, role: str, text: str, answer_type: str | None = None,
                       sources: list | None = None) -> None:
    pool = await get_pool()
    await pool.execute(
        "INSERT INTO dialog_messages (user_id, role, text, answer_type, sources) VALUES ($1,$2,$3,$4,$5)",
        user_id, role, text, answer_type, sources or [],
    )


async def recent_dialog(user_id: int, limit: int = 6) -> list[dict]:
    pool = await get_pool()
    rows = await pool.fetch(
        "SELECT role, text, answer_type FROM dialog_messages WHERE user_id = $1 ORDER BY created_at DESC LIMIT $2",
        user_id, limit,
    )
    return [dict(r) for r in reversed(rows)]


async def get_profile(user_id: int) -> dict | None:
    pool = await get_pool()
    row = await pool.fetchrow("SELECT * FROM business_profiles WHERE user_id = $1", user_id)
    return dict(row) if row else None


async def upsert_profile(user_id: int, **fields) -> None:
    if not fields:
        return
    pool = await get_pool()
    cols = list(fields)
    placeholders = ", ".join(f"${i + 2}" for i in range(len(cols)))
    updates = ", ".join(f"{c} = EXCLUDED.{c}" for c in cols)
    await pool.execute(
        f"""
        INSERT INTO business_profiles (user_id, {", ".join(cols)}) VALUES ($1, {placeholders})
        ON CONFLICT (user_id) DO UPDATE SET {updates}, updated_at = now()
        """,
        user_id, *fields.values(),
    )
