import json

import asyncpg
from pgvector.asyncpg import register_vector

from app.config import settings

_pool: asyncpg.Pool | None = None


async def _init_conn(conn: asyncpg.Connection) -> None:
    await register_vector(conn)
    await conn.set_type_codec("jsonb", encoder=json.dumps, decoder=json.loads, schema="pg_catalog")


async def _migrate(conn: asyncpg.Connection) -> None:
    """Небольшая idempotent-миграция для уже созданных Docker-томов."""
    await conn.execute("ALTER TABLE kb_documents ADD COLUMN IF NOT EXISTS stage TEXT")
    await conn.execute("ALTER TABLE kb_documents ADD COLUMN IF NOT EXISTS level TEXT")
    await conn.execute("ALTER TABLE kb_documents ADD COLUMN IF NOT EXISTS sphere TEXT")
    await conn.execute("""
        DELETE FROM reminders a USING reminders b
         WHERE a.id > b.id
           AND a.user_id = b.user_id AND a.kind = b.kind
           AND a.text = b.text AND a.next_run_at = b.next_run_at
    """)
    await conn.execute("""
        CREATE UNIQUE INDEX IF NOT EXISTS reminders_dedupe_idx
            ON reminders (user_id, kind, text, next_run_at)
    """)


async def get_pool() -> asyncpg.Pool:
    global _pool
    if _pool is None:
        _pool = await asyncpg.create_pool(settings.database_url, init=_init_conn, min_size=1, max_size=5)
        async with _pool.acquire() as conn:
            await _migrate(conn)
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


async def create_reminder(user_id: int, text: str, next_run_at, repeat_interval=None) -> int:
    pool = await get_pool()
    return await pool.fetchval(
        """INSERT INTO reminders (user_id, kind, text, next_run_at, repeat_interval)
           VALUES ($1, 'custom', $2, $3, $4) RETURNING id""",
        user_id, text, next_run_at, repeat_interval,
    )


async def list_reminders(user_id: int) -> list[dict]:
    pool = await get_pool()
    rows = await pool.fetch(
        """SELECT id, kind, text, next_run_at, repeat_interval
             FROM reminders
            WHERE user_id = $1 AND is_active
            ORDER BY next_run_at LIMIT 20""",
        user_id,
    )
    return [dict(r) for r in rows]


async def cancel_reminder(user_id: int, reminder_id: int) -> bool:
    pool = await get_pool()
    status = await pool.execute(
        "UPDATE reminders SET is_active = FALSE WHERE id = $1 AND user_id = $2 AND is_active",
        reminder_id, user_id,
    )
    return status.endswith("1")
