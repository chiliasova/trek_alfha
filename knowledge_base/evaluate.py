"""Оценка качества поиска бота на реальной базе kurs.alfabank.ru.

Подключение к боту:
    from app import rag
    chunks = await rag.search(query, k=k)   # list[Chunk]

Поиск асинхронный, поэтому оценка тоже асинхронная. Отказом считается
как пустой результат, так и лучший чанк ниже порога RAG_MIN_SIMILARITY
(по умолчанию 0.40). Это важно: иначе для вопросов вне базы бот вернёт
что-то похожее, а по факту откажется — и метрика refusal_rate будет
завышена.

Запуск:
    python -m knowledge_base.evaluate                  
    python -m knowledge_base.evaluate --search mock    
    python -m knowledge_base.evaluate --json report.json
"""
from __future__ import annotations

import argparse
import asyncio
import inspect
import json
import os
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Awaitable, Callable

BASE_DIR = Path(__file__).resolve().parent
COURSES_PATH = BASE_DIR / "data" / "real_courses.json"
BASE_URL = "https://kurs.alfabank.ru"

DEFAULT_MIN_SIMILARITY = 0.40


# test set

@dataclass
class TestCase:
    query: str
    expected_urls: list[str] = field(default_factory=list)
    kind: str = "direct"  # direct | out_of_base | ambiguous


def _u(path: str) -> str:
    return BASE_URL + path


def _norm(url: str | None) -> str:
    if not url:
        return ""
    return url.rstrip("/").lower()


TEST_SET: list[TestCase] = [
    # direct: 33 размеченных курса
    TestCase("как вести бизнес с Google Таблицами",            [_u("/courses/google-tablicy-dlya-biznesa-uproshchaem-rabotu-s-dannymi/")]),
    TestCase("с чего начать приём платежей в бизнесе",         [_u("/courses/azbuka-priyoma-platezhej/")]),
    TestCase("курс по финансовой грамотности для начинающих",  [_u("/courses/azy-finansovoj-gramotnosti/")]),
    TestCase("как запустить бизнес в сфере услуг",             [_u("/courses/biznes-bez-trevog-zapusk-proekta-v-sfere-uslug/")]),
    TestCase("как продавать товары на Авито",                  [_u("/courses/biznes-na-avito-lyogkaya-prodazha-tovarov/")]),
    TestCase("финансы и платежи при работе с Китаем",          [_u("/courses/biznes-s-kitaem/")]),
    TestCase("как выйти на Flowwow с цветами",                 [_u("/courses/buket-prodazh-vyhodim-na-flowwow/")]),
    TestCase("стать агентом банковских гарантий Альфа-Банка",  [_u("/courses/vedi-biznes-s-alfa-bankom-stan-agentom-bankovskih-garantij/")]),
    TestCase("продвижение и клиенты через Яндекс",             [_u("/courses/gde-brat-klientov-prodvizhenie-v-yandekse/")]),
    TestCase("где взять деньги на открытие бизнеса",           [_u("/courses/gde-vzyat-dengi-na-biznes/")]),
    TestCase("промптинг и генеративный ИИ",                    [_u("/courses/generativnyy-ii-kurs-po-promptingu/")]),
    TestCase("госзакупки теория и практика",                   [_u("/courses/goszakupki-teoriya-i-praktika/")]),
    TestCase("интернет-маркетинг для малого бизнеса",          [_u("/courses/internet-marketing-dlya-malogo-biznesa/")]),
    TestCase("курс для ИП, как строить бизнес",                [_u("/courses/ipeshniki-stroim-nekrinzhovyj-biznes/")]),
    TestCase("как самозанятому стать ИП",                      [_u("/courses/kak-samozanyatomu-stat-ip/")]),
    TestCase("как управлять командой",                         [_u("/courses/kak-upravlyat-komandoi/")]),
    TestCase("кейсы предпринимателей, как справиться с вызовами", [_u("/courses/keysy-predprinimateley-kak-spravitsya-s-vyzovami-v-biznese/")]),
    TestCase("как открыть маникюрную студию",                  [_u("/courses/biznes-bez-skolov-manikyurnaya-studiya-mechty/")]),
    TestCase("с чего начать продажи на маркетплейсах",         [_u("/courses/marketpleysy-s-chego-nachat-kak-preuspet/")]),
    TestCase("модель LBO пошагово",                            [_u("/courses/model-lbo-shag-za-shagom/")]),
    TestCase("как использовать наличные и не нарушить 115-ФЗ", [_u("/courses/nalichka-kak-ispolzovat-chtoby-ne-narushit-115-fz/")]),
    TestCase("подбор и адаптация команды продаж",              [_u("/courses/podbor-i-adaptaciya-komandy-prodazh/")]),
    TestCase("оплата по частям, как заработать на сервисе",    [_u("/courses/podeli-i-zarabotaj-rabota-s-servisom-oplaty-po-chastyam/")]),
    TestCase("первые шаги в бизнесе для новичков",             [_u("/courses/prosto-o-slozhnom/")]),
    TestCase("путь серийного предпринимателя",                 [_u("/courses/put-serijnogo-predprinimatelya-biznes-protiv-shersti/")]),
    TestCase("что важно знать о 115-ФЗ",                       [_u("/courses/vazhnoye-o-federalnom-zakone/")]),
    TestCase("торговля за рубежом и 115-ФЗ",                   [_u("/courses/torgovlya-za-rubezhom-kak-ne-narushit-115-fz/")]),
    TestCase("переводы денег и 115-ФЗ, транзит",               [_u("/courses/tranzit-kak-perevodit-dengi-chtoby-ne-narushit-115-fz/")]),
    TestCase("УСН на маркетплейсах, налоговый учёт",           [_u("/courses/usn-na-marketplejsah-osobennosti-nalogovogo-uchyota/")]),
    TestCase("бизнес-вызов, эксперт-вызов",                    [_u("/courses/biznes-vyzov/")]),
    TestCase("как увеличить продажи на маркетплейсах",         [_u("/courses/marketguru-iznutri-kak-uvelichit-prodazhi-na-marketplejsah/")]),
    TestCase("юридическая самопроверка бизнеса",               [_u("/courses/yuridicheskaya-samoproverka-biznesa/")]),
    TestCase("порядок в бизнесе с Яндекс 360",                 [_u("/courses/yandeks-360-poryadok-v-biznese-za-7-shagov/")]),

    # out_of_base: бот должен отказаться
    TestCase("как сварить борщ",                               kind="out_of_base"),
    TestCase("рецепт домашнего хлеба",                         kind="out_of_base"),
    TestCase("прогноз погоды в Москве на завтра",              kind="out_of_base"),
    TestCase("курс по астрономии для детей",                   kind="out_of_base"),
    TestCase("как ухаживать за кактусами",                     kind="out_of_base"),
    TestCase("изучение испанского языка с нуля",               kind="out_of_base"),
    TestCase("монтаж электрики в квартире",                    kind="out_of_base"),
    TestCase("тренировки по плаванию для взрослых",            kind="out_of_base"),
    TestCase("как выбрать фотоаппарат",                        kind="out_of_base"),
    TestCase("вязание носков спицами",                         kind="out_of_base"),
    TestCase("курсы игры на гитаре",                           kind="out_of_base"),
    TestCase("диета при гастрите",                             kind="out_of_base"),

    # ambiguous: несколько уместных курсов
    TestCase("курс про деньги", [
        _u("/courses/gde-vzyat-dengi-na-biznes/"),
        _u("/courses/azy-finansovoj-gramotnosti/"),
        _u("/courses/model-lbo-shag-za-shagom/"),
    ], kind="ambiguous"),
    TestCase("курс про маркетплейсы", [
        _u("/courses/marketpleysy-s-chego-nachat-kak-preuspet/"),
        _u("/courses/usn-na-marketplejsah-osobennosti-nalogovogo-uchyota/"),
        _u("/courses/marketguru-iznutri-kak-uvelichit-prodazhi-na-marketplejsah/"),
        _u("/courses/buket-prodazh-vyhodim-na-flowwow/"),
    ], kind="ambiguous"),
    TestCase("курс про 115-ФЗ", [
        _u("/courses/vazhnoye-o-federalnom-zakone/"),
        _u("/courses/nalichka-kak-ispolzovat-chtoby-ne-narushit-115-fz/"),
        _u("/courses/torgovlya-za-rubezhom-kak-ne-narushit-115-fz/"),
        _u("/courses/tranzit-kak-perevodit-dengi-chtoby-ne-narushit-115-fz/"),
    ], kind="ambiguous"),
    TestCase("курс про команду", [
        _u("/courses/kak-upravlyat-komandoi/"),
        _u("/courses/podbor-i-adaptaciya-komandy-prodazh/"),
    ], kind="ambiguous"),
    TestCase("курс про ИП", [
        _u("/courses/ipeshniki-stroim-nekrinzhovyj-biznes/"),
        _u("/courses/kak-samozanyatomu-stat-ip/"),
    ], kind="ambiguous"),
    TestCase("курс про финансы", [
        _u("/courses/azy-finansovoj-gramotnosti/"),
        _u("/courses/gde-vzyat-dengi-na-biznes/"),
        _u("/courses/model-lbo-shag-za-shagom/"),
        _u("/courses/biznes-s-kitaem/"),
    ], kind="ambiguous"),
]


# search adapter

SearchFn = Callable[[str, int], Awaitable[list[dict]]]


def _chunk_to_dict(c: Any) -> dict:
    """Приводит объект Chunk (dataclass / pydantic) или dict к словарю."""
    if isinstance(c, dict):
        url = c.get("url")
        title = c.get("title")
        score = c.get("score", 0.0)
    else:
        url = getattr(c, "url", None)
        title = getattr(c, "title", None)
        score = getattr(c, "score", 0.0)
    try:
        score_f = float(score) if score is not None else 0.0
    except (TypeError, ValueError):
        score_f = 0.0
    return {"url": url, "title": title, "score": score_f}


def _get_threshold() -> float:
    try:
        from app.config import settings  # type: ignore
        return float(getattr(settings, "min_similarity", DEFAULT_MIN_SIMILARITY))
    except Exception:
        return float(os.getenv("RAG_MIN_SIMILARITY", str(DEFAULT_MIN_SIMILARITY)))


def make_real_search(pool: Any | None) -> SearchFn:
    """Возвращает адаптер над rag.search. Если pool нужен сигнатуре —
    передаётся, если нет — вызывается без него."""
    try:
        from app import rag  # type: ignore
    except ImportError as exc:
        raise SystemExit(
            "Не удалось импортировать app.rag. Запускайте внутри окружения "
            "бота или используйте --search mock для smoke-теста."
        ) from exc

    try:
        sig = inspect.signature(rag.search)
        accepts_pool = "pool" in sig.parameters
    except (TypeError, ValueError):
        accepts_pool = False

    async def _search(query: str, k: int) -> list[dict]:
        kwargs: dict[str, Any] = {"k": k}
        if accepts_pool and pool is not None:
            kwargs["pool"] = pool
        chunks = await rag.search(query, **kwargs)
        return [_chunk_to_dict(c) for c in (chunks or [])]

    return _search


def make_mock_search() -> SearchFn:
    """Детерминированная заглушка."""
    courses: list[dict] = []
    if COURSES_PATH.exists():
        courses = json.loads(COURSES_PATH.read_text(encoding="utf-8"))

    async def _search(query: str, k: int) -> list[dict]:
        q = query.lower()
        for c in courses:
            if q in c["title"].lower():
                return [{"url": c["url"], "title": c["title"], "score": 1.0}]
        return []

    return _search


# metrics

def _urls(results: list[dict]) -> list[str]:
    return [_norm(r.get("url")) for r in results]


def _effective(results: list[dict], threshold: float) -> list[dict]:
    """Отсекает результаты ниже порога — так же, как это делает бот."""
    return [r for r in results if r.get("score", 0.0) >= threshold]


async def run_eval(
    search_fn: SearchFn,
    k: int = 3,
    threshold: float | None = None,
    test_set: list[TestCase] | None = None,
) -> dict:
    threshold = _get_threshold() if threshold is None else threshold
    test_set = test_set or TEST_SET

    direct = [t for t in test_set if t.kind == "direct"]
    oob = [t for t in test_set if t.kind == "out_of_base"]
    amb = [t for t in test_set if t.kind == "ambiguous"]

    hit1 = hit3 = 0
    misses: list[dict] = []
    for t in direct:
        expected = {_norm(u) for u in t.expected_urls}
        raw = await search_fn(t.query, k)
        kept = _effective(raw, threshold)
        urls = _urls(kept)
        if urls and urls[0] in expected:
            hit1 += 1
        if any(u in expected for u in urls[:k]):
            hit3 += 1
        else:
            misses.append({
                "query": t.query, "kind": t.kind, "threshold": threshold,
                "expected": sorted(expected), "got": urls,
                "raw": [{"url": r.get("url"), "score": r.get("score")} for r in raw],
            })

    refusals = 0
    false_answers: list[dict] = []
    for t in oob:
        raw = await search_fn(t.query, k)
        kept = _effective(raw, threshold)
        if not kept:
            refusals += 1
        else:
            false_answers.append({
                "query": t.query, "threshold": threshold,
                "got": [{"url": r.get("url"), "score": r.get("score")} for r in kept],
            })

    amb_ok = 0
    amb_bad: list[dict] = []
    for t in amb:
        expected = {_norm(u) for u in t.expected_urls}
        raw = await search_fn(t.query, k)
        kept = _effective(raw, threshold)
        urls = _urls(kept)
        if any(u in expected for u in urls[:k]):
            amb_ok += 1
        else:
            amb_bad.append({
                "query": t.query, "expected": sorted(expected), "got": urls,
                "raw": [{"url": r.get("url"), "score": r.get("score")} for r in raw],
            })

    def ratio(n: int, d: int) -> float:
        return round(n / d, 3) if d else 0.0

    return {
        "k": k,
        "min_similarity": threshold,
        "direct_total": len(direct),
        "hit@1": ratio(hit1, len(direct)),
        "hit@3": ratio(hit3, len(direct)),
        "out_of_base_total": len(oob),
        "refusal_rate": ratio(refusals, len(oob)),
        "ambiguous_total": len(amb),
        "ambiguous_top3_ok": ratio(amb_ok, len(amb)),
        "misses": misses,
        "false_answers": false_answers,
        "ambiguous_misses": amb_bad,
    }


# CLI

async def _main_async(args: argparse.Namespace) -> dict:
    if args.search == "mock":
        search_fn = make_mock_search()
        report = await run_eval(search_fn, k=args.k, threshold=args.threshold)
        return report

    dsn = args.dsn or os.getenv("DATABASE_URL")
    if not dsn:
        raise SystemExit("Не задан DATABASE_URL; для smoke-теста: --search mock")

    import asyncpg 

    pool = await asyncpg.create_pool(dsn, min_size=1, max_size=3)
    try:
        search_fn = make_real_search(pool)
        report = await run_eval(search_fn, k=args.k, threshold=args.threshold)
    finally:
        await pool.close()
    return report


def main() -> None:
    parser = argparse.ArgumentParser(description="Оценка поиска бота")
    parser.add_argument("--search", choices=["real", "mock"], default="real")
    parser.add_argument("--k", type=int, default=3)
    parser.add_argument("--threshold", type=float, default=None,
                        help="порог сходства; по умолчанию из настроек бота")
    parser.add_argument("--json", type=Path, default=None)
    parser.add_argument("--dsn", default=None)
    args = parser.parse_args()

    report = asyncio.run(_main_async(args))

    printable = {k: v for k, v in report.items() if not isinstance(v, list)}
    print(json.dumps(printable, ensure_ascii=False, indent=2))

    if report["misses"]:
        print("\nПромахи direct:")
        for m in report["misses"]:
            print(" -", m["query"], "->", m["got"] or "[]")
            if m.get("raw") and not m["got"]:
                print("     (сырые скоры ниже порога:", m["raw"], ")")
    if report["false_answers"]:
        print("\nЛожные ответы на out_of_base:")
        for m in report["false_answers"]:
            print(" -", m["query"], "->", m["got"])
    if report["ambiguous_misses"]:
        print("\nПромахи ambiguous:")
        for m in report["ambiguous_misses"]:
            print(" -", m["query"], "->", m["got"])

    if args.json:
        args.json.write_text(json.dumps(report, ensure_ascii=False, indent=2),
                             encoding="utf-8")
        print(f"\nОтчёт сохранён: {args.json}")


if __name__ == "__main__":
    main()