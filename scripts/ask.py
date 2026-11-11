"""Задать вопросы боту из консоли, без Telegram: python -m scripts.ask "вопрос" ["вопрос 2" ...]"""
import asyncio
import sys
import time

from app import rag
from app.db import close_pool

DEFAULT_QUESTIONS = [
    "Как ИП на УСН уменьшить налог на страховые взносы?",
    "Какой налог платить?",
    "Как зарегистрировать товарный знак?",
    "Что такое ЕНС?",
    "Как вырастить кактус?",
]


async def main(questions: list[str]) -> None:
    for q in questions:
        t = time.time()
        chunks = await rag.search(q)
        res = await rag.answer(q)
        print("=" * 80)
        print(f"Вопрос: {q}")
        print(f"Лучшая близость: {max((c.score for c in chunks), default=0):.3f}   [{time.time() - t:.1f} с]")
        print(f"Тип: {res.kind}\n{res.text}")
        for s in res.sources:
            print(f"  [{s['n']}] {s['title']} ({s['content_type']}{', платный' if s['is_paid'] else ''}) {s['url']}")
    await close_pool()


if __name__ == "__main__":
    asyncio.run(main(sys.argv[1:] or DEFAULT_QUESTIONS))
