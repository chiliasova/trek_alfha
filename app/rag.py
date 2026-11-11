import re
from dataclasses import dataclass, field

import numpy as np

from app import llm
from app.config import settings
from app.db import get_pool
from app.prompts import NOT_FOUND_TEXT, SYSTEM_PROMPT, USER_TEMPLATE

PROFILE_LABELS = {
    "stage": "Стадия бизнеса",
    "industry": "Сфера",
    "tax_regime": "Налоговый режим",
    "legal_form": "Форма",
    "has_staff": "Есть сотрудники",
    "current_goal": "Текущая задача",
    "experience": "Опыт",
}


@dataclass
class Chunk:
    chunk_id: int
    document_id: int
    title: str
    url: str | None
    content_type: str
    is_paid: bool
    text: str
    score: float


@dataclass
class Answer:
    kind: str  # answer | clarify | not_found
    text: str
    sources: list[dict] = field(default_factory=list)


async def search(query: str, k: int | None = None) -> list[Chunk]:
    """Гибридный поиск: косинусная близость эмбеддингов + полнотекстовый поиск, слияние через RRF."""
    k = k or settings.top_k
    [qvec] = await llm.embed([query], query=True)
    pool = await get_pool()
    vec_rows = await pool.fetch(
        """
        SELECT c.id, c.document_id, c.text, d.title, d.url, d.content_type, d.is_paid,
               1 - (c.embedding <=> $1) AS sim
        FROM kb_chunks c JOIN kb_documents d ON d.id = c.document_id
        WHERE c.embedding IS NOT NULL
        ORDER BY c.embedding <=> $1
        LIMIT $2
        """,
        np.array(qvec, dtype=np.float32), k * 3,
    )
    fts_rows = await pool.fetch(
        """
        SELECT c.id, c.document_id, c.text, d.title, d.url, d.content_type, d.is_paid,
               ts_rank(c.tsv, q) AS rank
        FROM kb_chunks c JOIN kb_documents d ON d.id = c.document_id,
             websearch_to_tsquery('russian', $1) q
        WHERE c.tsv @@ q
        ORDER BY rank DESC
        LIMIT $2
        """,
        query, k * 3,
    )

    sims = {r["id"]: float(r["sim"]) for r in vec_rows}
    rows = {r["id"]: r for r in [*vec_rows, *fts_rows]}
    rrf: dict[int, float] = {}
    for ranked in (vec_rows, fts_rows):
        for pos, r in enumerate(ranked):
            rrf[r["id"]] = rrf.get(r["id"], 0.0) + 1.0 / (60 + pos)

    best = sorted(rrf, key=rrf.get, reverse=True)[:k]
    return [
        Chunk(
            chunk_id=cid,
            document_id=rows[cid]["document_id"],
            title=rows[cid]["title"],
            url=rows[cid]["url"],
            content_type=rows[cid]["content_type"],
            is_paid=rows[cid]["is_paid"],
            text=rows[cid]["text"],
            score=sims.get(cid, 0.0),
        )
        for cid in best
    ]


def _format_profile(profile: dict | None) -> str:
    if not profile:
        return "не заполнен"
    parts = [f"{label}: {profile[key]}" for key, label in PROFILE_LABELS.items() if profile.get(key) not in (None, "")]
    return "; ".join(parts) or "не заполнен"


def _group_sources(chunks: list[Chunk]) -> list[dict]:
    """Один номер источника на документ, чтобы ссылки не дублировались."""
    sources: list[dict] = []
    seen: dict[int, int] = {}
    for ch in chunks:
        if ch.document_id not in seen:
            seen[ch.document_id] = len(sources) + 1
            sources.append({
                "n": seen[ch.document_id],
                "document_id": ch.document_id,
                "title": ch.title,
                "url": ch.url,
                "content_type": ch.content_type,
                "is_paid": ch.is_paid,
                "texts": [],
            })
        sources[seen[ch.document_id] - 1]["texts"].append(ch.text)
    return sources


def _build_context(sources: list[dict]) -> str:
    blocks = []
    for s in sources:
        paid = " [ПЛАТНЫЙ]" if s["is_paid"] else ""
        body = "\n".join(s["texts"])
        blocks.append(f"[{s['n']}] {s['title']}{paid}\n{body}")
    return "\n\n".join(blocks)


async def answer(question: str, profile: dict | None = None, history: list[dict] | None = None,
                 search_query: str | None = None) -> Answer:
    chunks = await search(search_query or question)
    if not chunks or max(c.score for c in chunks) < settings.min_similarity:
        return Answer("not_found", NOT_FOUND_TEXT)

    sources = _group_sources(chunks)
    messages = [{"role": "system", "content": SYSTEM_PROMPT}]
    for m in history or []:
        messages.append({"role": m["role"], "content": m["text"]})
    messages.append({
        "role": "user",
        "content": USER_TEMPLATE.format(
            profile=_format_profile(profile),
            context=_build_context(sources),
            question=search_query or question,
        ),
    })
    raw = await llm.chat(messages)

    if raw.startswith("NOT_FOUND"):
        return Answer("not_found", NOT_FOUND_TEXT)
    if raw.startswith("CLARIFY"):
        return Answer("clarify", raw.split(":", 1)[-1].strip())

    used = {int(n) for n in re.findall(r"\[(\d+)\]", raw)}
    cited = [s for s in sources if s["n"] in used] or sources[:2]
    for s in cited:
        s.pop("texts", None)
    return Answer("answer", raw, cited)
