"""Фоновая доставка разовых и повторяющихся напоминаний Telegram-пользователям."""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone

from aiogram import Bot

from app import db

log = logging.getLogger(__name__)
POLL_SECONDS = 30


async def _claim_due(pool, limit: int = 50) -> list[dict]:
    """Атомарно забирает due reminders, чтобы несколько workers не отправили дубль."""
    async with pool.acquire() as conn:
        async with conn.transaction():
            rows = await conn.fetch(
                """
                SELECT id, user_id, text, next_run_at, repeat_interval
                  FROM reminders
                 WHERE is_active AND next_run_at <= now()
                 ORDER BY next_run_at
                 FOR UPDATE SKIP LOCKED
                 LIMIT $1
                """,
                limit,
            )
            claimed = []
            for row in rows:
                if row["repeat_interval"] is None:
                    await conn.execute(
                        "UPDATE reminders SET is_active = FALSE, last_sent_at = now() WHERE id = $1",
                        row["id"],
                    )
                else:
                    # Сдвигаем срок до будущего, сохраняя исходный ритм.
                    await conn.execute(
                        """
                        UPDATE reminders
                           SET next_run_at = CASE
                               WHEN next_run_at + repeat_interval > now()
                               THEN next_run_at + repeat_interval
                               ELSE now() + repeat_interval
                               END,
                               last_sent_at = now()
                         WHERE id = $1
                        """,
                        row["id"],
                    )
                claimed.append(dict(row))
            return claimed


async def deliver_due(bot: Bot) -> int:
    pool = await db.get_pool()
    reminders = await _claim_due(pool)
    sent = 0
    for reminder in reminders:
        try:
            await bot.send_message(
                reminder["user_id"],
                f"⏰ Напоминание\n{reminder['text']}",
            )
            await db.log_event(reminder["user_id"], "reminder_sent", {"reminder_id": reminder["id"]})
            sent += 1
        except Exception:  # noqa: BLE001 — одна ошибка не должна останавливать worker
            log.exception("Не удалось отправить reminder %s", reminder["id"])
    return sent


async def worker(bot: Bot, stop: asyncio.Event) -> None:
    """Периодически доставляет напоминания до остановки приложения."""
    while not stop.is_set():
        try:
            await deliver_due(bot)
        except Exception:  # noqa: BLE001
            log.exception("Reminder worker iteration failed")
        try:
            await asyncio.wait_for(stop.wait(), timeout=POLL_SECONDS)
        except asyncio.TimeoutError:
            pass
