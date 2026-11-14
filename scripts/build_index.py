"""Строит индекс базы знаний из выгрузки «Альфа-Курса».

1. Читает материалы из базы alfa_source (восстановленный .backup).
2. Чистит HTML и JSON-блоки, режет текст на фрагменты.
3. Сохраняет документы и фрагменты в kb_documents / kb_chunks.
4. Считает эмбеддинги для фрагментов без эмбеддинга (можно прервать и продолжить).

Платные материалы (курсы с ценой или по подписке «Курс+», платные подкасты)
индексируются только по названию и описанию: полные тексты платных уроков
в индекс и в LLM не попадают (ограничения кейса 06 и 07).
"""
import asyncio
import html
import json
import logging
import re
import time

import asyncpg
import numpy as np
from pgvector.asyncpg import register_vector

from app import llm
from app.config import settings

log = logging.getLogger("build_index")

PLATFORM = "https://kurs.alfabank.ru"
CHUNK_SIZE = 1200
CHUNK_OVERLAP = 150
EMBED_BATCH = 32
PUBLISHED = "Опубликовано"

TEXT_KEYS = {"text", "description", "title", "items", "fileName"}
TAG_RE = re.compile(r"<[^>]+>")
BLOCK_TAG_RE = re.compile(r"</?(p|div|h[1-6]|li|ul|ol|br|tr|table)[^>]*>", re.I)


def html_to_text(raw: str | None) -> str:
    if not raw:
        return ""
    text = BLOCK_TAG_RE.sub("\n", raw)
    text = TAG_RE.sub("", text)
    text = html.unescape(text).replace("﻿", "").replace("\xa0", " ")
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n\s*\n+", "\n", text)
    return text.strip()


def params_to_text(raw: str | None) -> str:
    """Достаёт человекочитаемый текст из JSON-параметров блока (врезки, списки, цитаты, FAQ)."""
    if not raw:
        return ""
    try:
        data = json.loads(raw)
    except (ValueError, TypeError):
        return ""
    out: list[str] = []

    def walk(node, key=None):
        if isinstance(node, dict):
            for k, v in node.items():
                if k in ("item", "image", "images", "video"):  # карточки-ссылки и медиа пропускаем
                    continue
                walk(v, k)
        elif isinstance(node, list):
            for v in node:
                walk(v, key)
        elif isinstance(node, str) and key in TEXT_KEYS and node.strip():
            out.append(html_to_text(node))

    walk(data)
    return "\n".join(t for t in out if t)


def block_text(html_content: str | None, params: str | None) -> str:
    return "\n".join(t for t in (html_to_text(html_content), params_to_text(params)) if t)


def split_text(text: str, size: int = CHUNK_SIZE, overlap: int = CHUNK_OVERLAP) -> list[str]:
    paragraphs = [p.strip() for p in text.split("\n") if p.strip()]
    chunks: list[str] = []
    current = ""
    for p in paragraphs:
        while len(p) > size:  # очень длинный абзац режем по предложениям/символам
            cut = p.rfind(". ", 0, size)
            cut = cut + 1 if cut > size // 2 else size
            if current:
                chunks.append(current)
                current = ""
            chunks.append(p[:cut].strip())
            p = p[cut:].strip()
        if len(current) + len(p) + 1 > size and current:
            chunks.append(current)
            current = current[-overlap:].split(" ", 1)[-1] + "\n" + p if overlap else p
        else:
            current = f"{current}\n{p}" if current else p
    if current:
        chunks.append(current)
    return chunks


def url(section: str, slug: str | None) -> str | None:
    return f"{PLATFORM}/{section}/{slug.strip('/')}/" if slug else None


# ---------- Чтение материалов из выгрузки ----------

async def load_blocks(src: asyncpg.Connection, table: str, fk: str) -> dict:
    rows = await src.fetch(
        f'SELECT "{fk}" AS owner, "HtmlContent" AS h, "Params" AS p FROM "{table}" ORDER BY "{fk}", "Order"'
    )
    blocks: dict = {}
    for r in rows:
        t = block_text(r["h"], r["p"])
        if t:
            blocks.setdefault(r["owner"], []).append(t)
    return blocks


async def collect_documents(src: asyncpg.Connection) -> list[dict]:
    docs: list[dict] = []
    status_ok = f"(SELECT \"Id\" FROM \"Statuses\" WHERE \"Title\" = '{PUBLISHED}')"

    # Статьи
    blocks = await load_blocks(src, "ArticleEmbedItems", "ArticleId")
    rows = await src.fetch(f"""
        SELECT a."Id", a."Title", a."Description", a."Content", a."Url", s."Title" AS subject
        FROM "Articles" a LEFT JOIN "ArticleSubjects" s ON s."Id" = a."SubjectId"
        WHERE NOT a."IsDeleted" AND a."StatusId" IN {status_ok} AND NOT a."IsClosed"
    """)
    for r in rows:
        body = "\n".join(blocks.get(r["Id"], [])) or html_to_text(r["Content"])
        docs.append(dict(source_table="Articles", source_id=str(r["Id"]), content_type="article",
                         title=r["Title"], description=html_to_text(r["Description"]),
                         url=url("articles", r["Url"]), is_paid=False,
                         topics=[r["subject"]] if r["subject"] else [], body=body))

    # Курсы: платные = цена > 0 или входят в подписку
    rows = await src.fetch(f"""
        SELECT c."Id", c."Title", c."Description", c."DescriptionAdditional", c."Target", c."Url",
               COALESCE(c."Cost", 0) AS cost,
               EXISTS (SELECT 1 FROM "CourseSubscriptions" cs WHERE cs."CourseId" = c."Id") AS by_subscription
        FROM "Courses" c
        WHERE NOT c."IsDeleted" AND c."StatusId" IN {status_ok}
    """)
    courses = {}
    for r in rows:
        paid = r["cost"] > 0 or r["by_subscription"]
        courses[r["Id"]] = dict(title=r["Title"], url=url("courses", r["Url"]), paid=paid)
        price = f"Стоимость: {r['cost']} ₽." if r["cost"] else ("Доступен по подписке «Курс+»." if paid else "Бесплатный курс.")
        desc = "\n".join(filter(None, [html_to_text(r["Description"]), html_to_text(r["DescriptionAdditional"]),
                                       html_to_text(r["Target"]), price]))
        docs.append(dict(source_table="Courses", source_id=str(r["Id"]), content_type="course",
                         title=r["Title"], description=desc, url=courses[r["Id"]]["url"],
                         is_paid=paid, topics=[], body=""))

    # Уроки бесплатных курсов (тексты платных уроков не индексируем)
    blocks = await load_blocks(src, "LessonEmbedItems", "LessonId")
    rows = await src.fetch("""
        SELECT DISTINCT ON (l."Id") l."Id", l."Title", l."Description", l."Content", b."CourseId"
        FROM "Lessons" l JOIN "CourseLessonBindings" b ON b."LessonId" = l."Id"
        WHERE NOT l."IsDeleted" AND NOT b."IsDeleted" AND NOT l."IsGroup"
    """)
    for r in rows:
        course = courses.get(r["CourseId"])
        if not course or course["paid"]:
            continue
        body = "\n".join(blocks.get(r["Id"], [])) or html_to_text(r["Content"])
        if not body:
            continue
        docs.append(dict(source_table="Lessons", source_id=str(r["Id"]), content_type="lesson",
                         title=f"{course['title']}: {r['Title']}", description=html_to_text(r["Description"]),
                         url=course["url"], is_paid=False, topics=[], body=body))

    # Документы и шаблоны
    blocks = await load_blocks(src, "DocumentEmbedItems", "DocumentId")
    rows = await src.fetch(f"""
        SELECT "Id", "Title", "Description", "Url" FROM "Documents"
        WHERE NOT "IsDeleted" AND ("StatusId" IS NULL OR "StatusId" IN {status_ok})
    """)
    for r in rows:
        docs.append(dict(source_table="Documents", source_id=str(r["Id"]), content_type="document",
                         title=r["Title"], description=html_to_text(r["Description"]),
                         url=url("documents", r["Url"]), is_paid=False, topics=[],
                         body="\n".join(blocks.get(r["Id"], []))))

    # Глоссарий
    blocks = await load_blocks(src, "GlossaryEmbedItems", "GlossaryId")
    rows = await src.fetch(f"""
        SELECT "Id", "Title", "Description", "Url" FROM "Glossary"
        WHERE NOT "IsDeleted" AND "StatusId" IN {status_ok}
    """)
    for r in rows:
        docs.append(dict(source_table="Glossary", source_id=str(r["Id"]), content_type="glossary",
                         title=r["Title"], description=html_to_text(r["Description"]),
                         url=url("vocabulary", r["Url"]), is_paid=False, topics=[],
                         body="\n".join(blocks.get(r["Id"], []))))

    # Вебинары и подкасты: только описания
    for table, ctype, section in (("Webinars", "webinar", "webinars"), ("Podcasts", "podcast", "podcasts")):
        cost_col = 'COALESCE("Cost", 0)' if table == "Podcasts" else "0"
        rows = await src.fetch(f"""
            SELECT "Id", "Title", "Description", "DescriptionExt", "Url", {cost_col} AS cost FROM "{table}"
            WHERE NOT "IsDeleted" AND "StatusId" IN {status_ok}
        """)
        for r in rows:
            desc = "\n".join(filter(None, [html_to_text(r["Description"]), html_to_text(r["DescriptionExt"])]))
            docs.append(dict(source_table=table, source_id=str(r["Id"]), content_type=ctype,
                             title=r["Title"], description=desc, url=url(section, r["Url"]),
                             is_paid=r["cost"] > 0, topics=[], body=""))
    return docs


# ---------- Запись индекса ----------

async def write_documents(conn: asyncpg.Connection, docs: list[dict]) -> int:
    n_chunks = 0
    async with conn.transaction():
        for d in docs:
            doc_id = await conn.fetchval(
                """
                INSERT INTO kb_documents (source_table, source_id, content_type, title, description, url, is_paid, topics)
                VALUES ($1,$2,$3,$4,$5,$6,$7,$8)
                ON CONFLICT (source_table, source_id) DO UPDATE SET title = EXCLUDED.title
                RETURNING id
                """,
                d["source_table"], d["source_id"], d["content_type"], d["title"], d["description"],
                d["url"], d["is_paid"], d["topics"],
            )
            text = "\n".join(filter(None, [d["description"], d["body"]]))
            if not text:
                text = d["title"]
            for i, piece in enumerate(split_text(text)):
                await conn.execute(
                    "INSERT INTO kb_chunks (document_id, chunk_index, text) VALUES ($1,$2,$3) "
                    "ON CONFLICT (document_id, chunk_index) DO NOTHING",
                    doc_id, i, f"{d['title']}\n{piece}",
                )
                n_chunks += 1
    return n_chunks


async def embed_missing(conn: asyncpg.Connection) -> None:
    total = await conn.fetchval("SELECT count(*) FROM kb_chunks WHERE embedding IS NULL")
    done, started = 0, time.time()
    while True:
        rows = await conn.fetch(
            "SELECT id, text FROM kb_chunks WHERE embedding IS NULL ORDER BY id LIMIT $1", EMBED_BATCH
        )
        if not rows:
            break
        vectors = await llm.embed([r["text"] for r in rows])
        await conn.executemany(
            "UPDATE kb_chunks SET embedding = $2 WHERE id = $1",
            [(r["id"], np.array(v, dtype=np.float32)) for r, v in zip(rows, vectors)],
        )
        done += len(rows)
        if done % (EMBED_BATCH * 10) == 0 or done == total:
            rate = done / max(time.time() - started, 1e-6)
            log.info("Эмбеддинги: %d/%d (%.1f фрагм./с)", done, total, rate)


async def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    logging.getLogger("httpx").setLevel(logging.WARNING)
    conn = await asyncpg.connect(settings.database_url)
    await register_vector(conn)

    if await conn.fetchval("SELECT count(*) FROM kb_documents") == 0:
        try:
            src = await asyncpg.connect(settings.source_database_url)
        except Exception as exc:  # noqa: BLE001
            raise SystemExit(
                f"Не удалось подключиться к базе выгрузки ({exc}). "
                "Положите alfa_course_hackathon.backup в папку data/ и выполните docker compose down -v && docker compose up --build"
            )
        docs = await collect_documents(src)
        await src.close()
        n = await write_documents(conn, docs)
        log.info("Документов: %d, фрагментов: %d", len(docs), n)
    else:
        log.info("Документы уже загружены, досчитываю эмбеддинги")

    # При смене модели эмбеддингов старые векторы несовместимы: сбрасываем и считаем заново
    await conn.execute("CREATE TABLE IF NOT EXISTS kb_meta (key TEXT PRIMARY KEY, value TEXT NOT NULL)")
    prev = await conn.fetchval("SELECT value FROM kb_meta WHERE key = 'embed_model'")
    if prev and prev != settings.embed_model:
        log.info("Модель эмбеддингов сменилась (%s -> %s), пересчитываю", prev, settings.embed_model)
        await conn.execute("UPDATE kb_chunks SET embedding = NULL")
    await conn.execute(
        "INSERT INTO kb_meta VALUES ('embed_model', $1) ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value",
        settings.embed_model,
    )

    await embed_missing(conn)
    log.info("Индекс готов")
    await conn.close()


if __name__ == "__main__":
    asyncio.run(main())
