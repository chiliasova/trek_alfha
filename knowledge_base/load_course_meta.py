"""Заполняет stage / level / sphere у курсов в kb_documents.

Источник — data/real_courses.json. Скрипт не создаёт курсы, а обновляет
уже проиндексированные: сопоставление по нормализованному url.

Запуск (внутри окружения бота, где есть DATABASE_URL):
    python -m knowledge_base.load_course_meta
    python -m knowledge_base.load_course_meta --dry-run
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
from pathlib import Path
from urllib.parse import urlparse, urlunparse

import asyncpg

BASE_DIR = Path(__file__).resolve().parent
COURSES_PATH = BASE_DIR / "data" / "real_courses.json"


def normalize_url(url: str) -> str:
    """Единая нормализация: без query, без trailing slash."""
    parsed = urlparse(url.strip())
    path = parsed.path.rstrip("/")
    return urlunparse((parsed.scheme, parsed.netloc.lower(), path, "", "", ""))


async def apply(pool: asyncpg.Pool, dry_run: bool = False) -> dict:
    courses = json.loads(COURSES_PATH.read_text(encoding="utf-8"))
    updated = 0
    not_found: list[str] = []

    for c in courses:
        url = normalize_url(c["url"])
        row = await pool.fetchrow(
            "SELECT id FROM kb_documents WHERE content_type = 'course' AND url = $1",
            url,
        )
        if not row:
            row = await pool.fetchrow(
                "SELECT id FROM kb_documents WHERE content_type = 'course' AND rtrim(url, '/') = $1",
                url,
            )
        if not row:
            not_found.append(c["url"])
            continue

        if dry_run:
            updated += 1
            continue

        await pool.execute(
            """UPDATE kb_documents
                  SET stage = $1, level = $2, sphere = $3
                WHERE id = $4""",
            c["stage"], c["level"], c.get("sphere"), row["id"],
        )
        updated += 1

    return {"updated": updated, "not_found": not_found, "total": len(courses)}


async def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--dsn", default=os.getenv("DATABASE_URL"))
    args = parser.parse_args()

    if not args.dsn:
        raise SystemExit("Не задан DATABASE_URL")

    pool = await asyncpg.create_pool(args.dsn, min_size=1, max_size=3)
    try:
        result = await apply(pool, dry_run=args.dry_run)
    finally:
        await pool.close()

    print(json.dumps(result, ensure_ascii=False, indent=2))
    if result["not_found"]:
        print("\nНе найдены в kb_documents (проверьте url):")
        for u in result["not_found"]:
            print(" -", u)


if __name__ == "__main__":
    asyncio.run(main())