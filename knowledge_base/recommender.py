"""Рекомендатель курсов на реальных данных из kb_documents.

Работает через asyncpg.Pool. Профиль пользователя берётся из
business_profiles (stage / experience / industry / access) — либо
передаётся явно словарём. Скоринг — по стадии, уровню, сфере и
бесплатности; каждое решение сопровождается причинами.

Запуск для ручной проверки:
    python -m knowledge_base.recommender --stage запуск --level новичок --sphere маркетплейсы
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
from pathlib import Path
from typing import Any

import asyncpg

BASE_DIR = Path(__file__).resolve().parent
COURSES_PATH = BASE_DIR / "data" / "real_courses.json"

NEIGHBOUR_STAGES = {
    "идея": {"идея", "запуск"},
    "запуск": {"идея", "запуск", "работаю"},
    "работаю": {"запуск", "работаю", "рост"},
    "рост": {"работаю", "рост"},
}

# маппинг значений из business_profiles в термины разметки
STAGE_MAP = {
    "idea": "идея",
    "launch": "запуск",
    "running": "работаю",
    "growth": "рост",
}
LEVEL_MAP = {
    "novice": "новичок",
    "beginner": "новичок",
    "experienced": "опытный",
    "advanced": "опытный",
}


def profile_from_business_profile(row: dict[str, Any]) -> dict[str, Any]:
    """Преобразует строку business_profiles в профиль для скоринга."""
    stage_raw = (row.get("stage") or "").lower()
    exp_raw = (row.get("experience") or "").lower()
    return {
        "stage": STAGE_MAP.get(stage_raw, stage_raw or None),
        "level": LEVEL_MAP.get(exp_raw, exp_raw or None),
        "sphere": (row.get("industry") or None),
        "access": "бесплатный" if row.get("paid_only") is False else None,
    }


# загрузка

async def _load_from_db(pool: asyncpg.Pool) -> list[dict]:
    """Читает курсы из kb_documents. Если колонок stage/level нет —
    откатывается на real_courses.json по url."""
    try:
        rows = await pool.fetch(
            """SELECT id, title, url, is_paid, stage, level, sphere
                 FROM kb_documents
                WHERE content_type = 'course'"""
        )
        return [
            {
                "id": r["id"], "title": r["title"], "url": r["url"],
                "access": "платный" if r["is_paid"] else "бесплатный",
                "stage": r["stage"], "level": r["level"], "sphere": r["sphere"],
            }
            for r in rows
        ]
    except asyncpg.UndefinedColumnError:
        rows = await pool.fetch(
            """SELECT id, title, url, is_paid
                 FROM kb_documents
                WHERE content_type = 'course'"""
        )
        base = {
            _norm(r["url"]): {
                "id": r["id"], "title": r["title"], "url": r["url"],
                "access": "платный" if r["is_paid"] else "бесплатный",
            }
            for r in rows
        }
        return _merge_with_json(base)


def _norm(url: str) -> str:
    return (url or "").rstrip("/").lower()


def _merge_with_json(base: dict[str, dict]) -> list[dict]:
    if not COURSES_PATH.exists():
        return list(base.values())
    courses = json.loads(COURSES_PATH.read_text(encoding="utf-8"))
    for c in courses:
        key = _norm(c["url"])
        if key in base:
            base[key].update({
                "stage": c.get("stage"),
                "level": c.get("level"),
                "sphere": c.get("sphere"),
            })
    return list(base.values())


def _load_from_json() -> list[dict]:
    courses = json.loads(COURSES_PATH.read_text(encoding="utf-8"))
    return [
        {
            "id": c["id"], "title": c["title"], "url": c["url"],
            "access": c.get("access"), "stage": c.get("stage"),
            "level": c.get("level"), "sphere": c.get("sphere"),
        }
        for c in courses
    ]


# скоринг

def score_one(profile: dict[str, Any], material: dict[str, Any]) -> tuple[float, list[str]]:
    score = 0.0
    reasons: list[str] = []

    p_stage = profile.get("stage")
    m_stage = material.get("stage")
    if p_stage and m_stage:
        if p_stage == m_stage:
            score += 3.0
            reasons.append(f"стадия совпадает: {p_stage}")
        elif m_stage in NEIGHBOUR_STAGES.get(p_stage, set()):
            score += 1.0
            reasons.append(f"стадия близка: {m_stage} (у вас {p_stage})")

    p_level = profile.get("level")
    if p_level and material.get("level") == p_level:
        score += 2.0
        reasons.append(f"уровень совпадает: {p_level}")

    p_sphere = profile.get("sphere")
    if p_sphere and material.get("sphere") == p_sphere:
        score += 2.0
        reasons.append(f"сфера совпадает: {p_sphere}")

    p_access = profile.get("access")
    if p_access and material.get("access") == p_access:
        score += 0.5
        if p_access == "бесплатный":
            reasons.append("бесплатный доступ")

    return score, reasons


def rank(profile: dict[str, Any], materials: list[dict], top_k: int = 5) -> list[dict]:
    scored: list[dict] = []
    for m in materials:
        s, reasons = score_one(profile, m)
        if s <= 0:
            continue
        scored.append({"material": m, "score": round(s, 2), "reasons": reasons})
    scored.sort(key=lambda x: x["score"], reverse=True)
    return scored[:top_k]


# публичное

async def recommend(
    pool: asyncpg.Pool | None,
    profile: dict[str, Any],
    top_k: int = 5,
) -> list[dict]:
    """Основная функция для бота. Если pool=None — читает JSON (dev/CLI)."""
    if pool is None:
        materials = _load_from_json()
    else:
        materials = await _load_from_db(pool)
    return rank(profile, materials, top_k=top_k)


async def recommend_for_user(
    pool: asyncpg.Pool,
    user_id: int,
    top_k: int = 5,
) -> list[dict]:
    """Удобная обёртка: берёт профиль из business_profiles."""
    row = await pool.fetchrow(
        "SELECT stage, experience, industry FROM business_profiles WHERE user_id = $1",
        user_id,
    )
    if not row:
        return []
    profile = profile_from_business_profile(dict(row))
    return await recommend(pool, profile, top_k=top_k)


# CLI

async def _main_async(args: argparse.Namespace) -> list[dict]:
    profile = {"stage": args.stage, "level": args.level,
               "sphere": args.sphere, "access": args.access}

    pool = None
    if args.dsn or os.getenv("DATABASE_URL"):
        pool = await asyncpg.create_pool(args.dsn or os.environ["DATABASE_URL"],
                                         min_size=1, max_size=3)
    try:
        return await recommend(pool, profile, top_k=args.top_k)
    finally:
        if pool is not None:
            await pool.close()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--stage", default=None)
    parser.add_argument("--level", default=None)
    parser.add_argument("--sphere", default=None)
    parser.add_argument("--access", default=None, choices=[None, "бесплатный", "платный"])
    parser.add_argument("--top-k", type=int, default=5)
    parser.add_argument("--dsn", default=None)
    args = parser.parse_args()

    result = asyncio.run(_main_async(args))
    for item in result:
        m = item["material"]
        print(f"{item['score']:>4}  {m['title']}")
        print(f"      {m['url']}")
        print(f"      причины: {', '.join(item['reasons'])}")
        print()


if __name__ == "__main__":
    main()