import re
from dataclasses import dataclass, field

import numpy as np

from app import llm
from app.config import settings
from app.db import get_pool
from app.prompts import NOT_FOUND_TEXT, SYSTEM_PROMPT, USER_TEMPLATE

PROFILE_LABELS = {
    "legal_form": "Форма",
    "tax_regime": "Налоговый режим",
    "stage": "Стадия бизнеса",
    "industry": "Сфера",
    "experience": "Опыт",
    "has_staff": "Есть сотрудники",
    "current_goal": "Текущая задача",
}

COURSE_INTENT_RE = re.compile(r"\bкурс|обучени|поучиться|учиться|подписк|вебинар", re.I)
PAID_INTENT_RE = re.compile(r"платн|купить|подписк", re.I)
SHORT_QUESTION_WORDS = 6
# Порог для подсказки «близких материалов», когда точного ответа нет
RELATED_MIN_SIMILARITY = 0.34
# Порог, с которого к ответу добавляется рекомендация курса
COURSE_RECOMMEND_SIMILARITY = 0.47


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
    kind: str  # answer | clarify | not_found | courses
    text: str
    sources: list[dict] = field(default_factory=list)
    recommendations: list[dict] = field(default_factory=list)


async def search(query: str, k: int | None = None, content_types: list[str] | None = None,
                 paid_only: bool = False) -> list[Chunk]:
    """Гибридный поиск: косинусная близость эмбеддингов + полнотекстовый поиск, слияние через RRF."""
    k = k or settings.top_k
    [qvec] = await llm.embed([query], query=True)
    pool = await get_pool()
    filters = "AND ($3::text[] IS NULL OR d.content_type = ANY($3)) AND (NOT $4 OR d.is_paid)"
    vec_rows = await pool.fetch(
        f"""
        SELECT c.id, c.document_id, c.text, d.title, d.url, d.content_type, d.is_paid,
               1 - (c.embedding <=> $1) AS sim
        FROM kb_chunks c JOIN kb_documents d ON d.id = c.document_id
        WHERE c.embedding IS NOT NULL {filters}
        ORDER BY c.embedding <=> $1
        LIMIT $2
        """,
        np.array(qvec, dtype=np.float32), k * 3, content_types, paid_only,
    )
    fts_rows = await pool.fetch(
        f"""
        SELECT c.id, c.document_id, c.text, d.title, d.url, d.content_type, d.is_paid,
               ts_rank(c.tsv, q) AS rank
        FROM kb_chunks c JOIN kb_documents d ON d.id = c.document_id,
             websearch_to_tsquery('russian', $1) q
        WHERE c.tsv @@ q {filters}
        ORDER BY rank DESC
        LIMIT $2
        """,
        query, k * 3, content_types, paid_only,
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


def _profile_keywords(profile: dict | None) -> str:
    """Ключевые слова профиля для поиска по коротким общим вопросам («Какой налог платить?»)."""
    if not profile:
        return ""
    return " ".join(str(profile[k]) for k in ("legal_form", "tax_regime") if profile.get(k))


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


def _renumber(text: str, sources: list[dict]) -> tuple[str, list[dict]]:
    """Оставляет только процитированные источники и нумерует их подряд: [6] -> [1]."""
    used = []
    for n in re.findall(r"\[(\d+)\]", text):
        if int(n) not in used and any(s["n"] == int(n) for s in sources):
            used.append(int(n))
    if not used:
        return text, [dict(s, texts=None) for s in sources[:2]]
    mapping = {old: new for new, old in enumerate(used, start=1)}
    text = re.sub(r"\[(\d+)\]", lambda m: f"[{mapping[int(m.group(1))]}]" if int(m.group(1)) in mapping else "", text)
    cited = []
    for old in used:
        s = next(s for s in sources if s["n"] == old)
        cited.append({k: v for k, v in s.items() if k != "texts"} | {"n": mapping[old]})
    return text, cited


async def _course_docs(query: str, k: int = 4, paid_only: bool = False) -> list[dict]:
    chunks = await search(query, k=k * 2, content_types=["course"], paid_only=paid_only)
    doc_ids, seen = [], set()
    for c in chunks:
        if c.document_id not in seen:
            seen.add(c.document_id)
            doc_ids.append((c.document_id, c.score))
    if not doc_ids:
        return []
    pool = await get_pool()
    rows = await pool.fetch(
        "SELECT id, title, url, is_paid, description FROM kb_documents WHERE id = ANY($1)",
        [d for d, _ in doc_ids],
    )
    by_id = {r["id"]: dict(r) for r in rows}
    out = []
    for doc_id, score in doc_ids[:k]:
        d = by_id[doc_id]
        lines = [ln for ln in (d["description"] or "").split("\n") if ln.strip()]
        price = next((ln for ln in lines if ln.startswith(("Стоимость", "Доступен по подписке", "Бесплатный курс"))), "")
        summary = next((ln for ln in lines if ln not in (price,)), "")
        summary = re.split(r"(?<=[.!?])\s", summary)[0][:220]
        out.append(dict(document_id=doc_id, title=d["title"], url=d["url"], is_paid=d["is_paid"],
                        price=price, summary=summary, score=score))
    return out


async def courses(query: str, profile: dict | None = None) -> Answer:
    """Подборка курсов под запрос и профиль."""
    paid_only = bool(PAID_INTENT_RE.search(query))
    search_q = " ".join(filter(None, [query, profile.get("industry") if profile else None,
                                      profile.get("current_goal") if profile else None]))
    items = await _course_docs(search_q, k=4, paid_only=paid_only)
    if not items:
        return Answer("not_found", "Подходящих курсов в каталоге не нашёл. Расскажите, какую задачу хотите решить?")
    intro = "Вот курсы «Альфа-Курса», которые подходят под ваш запрос"
    intro += " и профиль:" if profile else ":"
    return Answer("courses", intro, recommendations=items)


async def answer(question: str, profile: dict | None = None, history: list[dict] | None = None,
                 search_query: str | None = None) -> Answer:
    query = search_query or question
    if COURSE_INTENT_RE.search(question) and len(question.split()) <= 8:
        return await courses(query, profile)

    chunks = await search(query)
    best = max((c.score for c in chunks), default=0.0)
    # Решение «в базе нет» принимаем по самому вопросу, без добавок из профиля
    if best < settings.min_similarity:
        return await _not_found(chunks, best)

    retrieval_query = query
    if len(question.split()) <= SHORT_QUESTION_WORDS and _profile_keywords(profile):
        # Короткий общий вопрос: ищем с учётом формы бизнеса и налогового режима из профиля
        retrieval_query = f"{query} {_profile_keywords(profile)}"
        chunks = await search(retrieval_query)

    sources = _group_sources(chunks)
    messages = [{"role": "system", "content": SYSTEM_PROMPT}]
    for m in history or []:
        messages.append({"role": m["role"], "content": m["text"]})
    messages.append({
        "role": "user",
        "content": USER_TEMPLATE.format(
            profile=_format_profile(profile),
            context=_build_context(sources),
            question=query,
        ),
    })
    raw = await llm.chat(messages, temperature=0.1)

    if raw.startswith("NOT_FOUND"):
        return await _not_found(chunks, best)
    if raw.startswith("CLARIFY"):
        return Answer("clarify", raw.split(":", 1)[-1].strip())

    text, cited = _renumber(raw, sources)
    # Рекомендация курса по теме вопроса, если такой курс есть
    recs = [c for c in await _course_docs(retrieval_query, k=1) if c["score"] >= COURSE_RECOMMEND_SIMILARITY]
    return Answer("answer", text, cited, recs)


async def _not_found(chunks: list[Chunk], best: float) -> Answer:
    related = []
    if best >= RELATED_MIN_SIMILARITY:
        seen = set()
        for c in chunks:
            if c.document_id not in seen and c.score >= RELATED_MIN_SIMILARITY:
                seen.add(c.document_id)
                related.append({"n": len(related) + 1, "document_id": c.document_id, "title": c.title,
                                "url": c.url, "content_type": c.content_type, "is_paid": c.is_paid})
            if len(related) == 3:
                break
    return Answer("not_found", NOT_FOUND_TEXT, related)
