"""Налоговый календарь и запись напоминаний в reminders.

get_deadlines() — чистая функция, не зависит от БД.
create_reminders() — пишет дедлайны в reminders (kind='tax').

Даты фиксированные, без учёта переносов выходных. Для напоминаний
этого достаточно; если нужны точные переносы — подключается
производственный календарь.

Запуск для просмотра:
    python -m knowledge_base.tax_calendar --year 2026
"""
from __future__ import annotations

import argparse
import asyncio
import os
from dataclasses import asdict, dataclass
from datetime import date, datetime
from typing import Iterable

import asyncpg


@dataclass(frozen=True)
class Deadline:
    date: date
    title: str
    description: str
    regimes: tuple[str, ...]

    def to_dict(self) -> dict:
        d = asdict(self)
        d["date"] = self.date.isoformat()
        d["regimes"] = list(self.regimes)
        return d


# квартальные

def usn_deadlines(year: int) -> list[Deadline]:
    return [
        Deadline(date(year, 4, 28),  "УСН: аванс за I квартал",   "До 28 апреля",                 ("УСН",)),
        Deadline(date(year, 7, 28),  "УСН: аванс за полугодие",   "До 28 июля",                   ("УСН",)),
        Deadline(date(year, 10, 28), "УСН: аванс за 9 месяцев",   "До 28 октября",                ("УСН",)),
        Deadline(date(year + 1, 4, 28), "УСН: налог за год (ИП)", "До 28 апреля следующего года", ("УСН",)),
    ]


def osno_deadlines(year: int) -> list[Deadline]:
    return [
        Deadline(date(year, 4, 25),  "НДС за I квартал",   "До 25 апреля",                 ("ОСНО",)),
        Deadline(date(year, 7, 25),  "НДС за II квартал",  "До 25 июля",                   ("ОСНО",)),
        Deadline(date(year, 10, 25), "НДС за III квартал", "До 25 октября",                ("ОСНО",)),
        Deadline(date(year + 1, 1, 25), "НДС за IV квартал", "До 25 января следующего года", ("ОСНО",)),
        Deadline(date(year + 1, 4, 30), "НДФЛ ИП за год",  "До 30 апреля следующего года", ("ОСНО",)),
    ]


def npd_deadline(month: int, year: int) -> Deadline:
    nxt_month = month + 1
    nxt_year = year
    if nxt_month == 13:
        nxt_month, nxt_year = 1, year + 1
    return Deadline(
        date(nxt_year, nxt_month, 28),
        "НПД: налог за месяц",
        f"До 28 числа месяца, следующего за {month:02d}.{year}",
        ("НПД",),
    )


def patent_deadlines(issue: date, months: int) -> list[Deadline]:
    if months <= 6:
        return [Deadline(issue, "Патент: полная сумма",
                         f"Срок действия — {months} мес.", ("Патент",))]
    end_year = issue.year + (issue.month - 1 + months) // 12
    end_month = (issue.month - 1 + months) % 12 + 1
    end = date(end_year, end_month, issue.day)
    first = date.fromordinal(issue.toordinal() + 90)
    return [
        Deadline(first, "Патент: 1/3 стоимости", "В первые 90 дней действия", ("Патент",)),
        Deadline(end,   "Патент: остаток 2/3",   "До окончания срока действия", ("Патент",)),
    ]


# страховые взносы ИП

def ip_contributions_deadlines(year: int, extra_income: bool = True) -> list[Deadline]:
    """Фиксированные взносы ИП — 28 декабря; 1% с превышения 300 тыс. —
    до 1 июля следующего года. Платят все ИП независимо от режима."""
    items = [
        Deadline(
            date(year, 12, 28),
            "ИП: фиксированные страховые взносы",
            "Пенсионное и медицинское страхование за текущий год",
            ("ИП", "УСН", "Патент", "НПД", "ОСНО"),
        ),
    ]
    if extra_income:
        items.append(Deadline(
            date(year + 1, 7, 1),
            "ИП: 1% с дохода свыше 300 000 ₽",
            "Дополнительный взнос в ПФР за предыдущий год",
            ("ИП", "УСН", "Патент", "ОСНО"),
        ))
    return items


# общее

def get_deadlines(year: int, regimes: tuple[str, ...] | None = None) -> list[Deadline]:
    items: list[Deadline] = []
    items += usn_deadlines(year)
    items += osno_deadlines(year)
    items += ip_contributions_deadlines(year)
    items += [npd_deadline(m, year) for m in range(1, 13)]

    if regimes:
        wanted = set(regimes)
        items = [d for d in items if wanted.intersection(d.regimes)]

    return sorted(items, key=lambda d: d.date)


def next_deadline(today: date, regimes: tuple[str, ...] | None = None) -> Deadline | None:
    candidates = get_deadlines(today.year, regimes) + get_deadlines(today.year + 1, regimes)
    future = [d for d in candidates if d.date >= today]
    return future[0] if future else None


# запись в reminders (бот)

async def create_reminders(
    pool: asyncpg.Pool,
    user_id: int,
    year: int,
    regimes: tuple[str, ...] | None = None,
    today: date | None = None,
) -> int:
    """Идемпотентно пишет будущие налоговые дедлайны пользователю в reminders.

    Возвращает число вставленных записей. Повторные вызовы не создают
    дубликаты (проверка по user_id + kind='tax' + text + next_run_at).
    """
    today = today or date.today()
    deadlines = [d for d in get_deadlines(year, regimes) if d.date >= today]
    inserted = 0

    for d in deadlines:
        next_run = datetime(d.date.year, d.date.month, d.date.day, 10, 0)
        exists = await pool.fetchval(
            """SELECT 1 FROM reminders
                WHERE user_id = $1
                  AND kind = 'tax'
                  AND text = $2
                  AND next_run_at = $3
                LIMIT 1""",
            user_id, d.title, next_run,
        )
        if exists:
            continue
        await pool.execute(
            """INSERT INTO reminders (user_id, kind, text, next_run_at)
               VALUES ($1, 'tax', $2, $3)""",
            user_id, d.title, next_run,
        )
        inserted += 1

    return inserted


async def create_reminders_for_user(
    pool: asyncpg.Pool,
    user_id: int,
    year: int | None = None,
) -> int:
    """Обёртка: берёт налоговый режим из business_profiles и пишет reminders."""
    year = year or date.today().year
    tax_regime = await pool.fetchval(
        "SELECT tax_regime FROM business_profiles WHERE user_id = $1",
        user_id,
    )
    regimes = None
    if tax_regime:
        regimes = tuple(r for r in [tax_regime] if r) + ("ИП",)
    return await create_reminders(pool, user_id, year, regimes=regimes)


# CLI

def _print_deadlines(items: Iterable[Deadline]) -> None:
    for d in items:
        print(f"{d.date.isoformat()}  {d.title}")
        print(f"            {d.description}")
        print(f"            режимы: {', '.join(d.regimes)}")
        print()


async def _main_async(args: argparse.Namespace) -> None:
    if args.user_id is not None:
        dsn = args.dsn or os.getenv("DATABASE_URL")
        if not dsn:
            raise SystemExit("Не задан DATABASE_URL")
        pool = await asyncpg.create_pool(dsn, min_size=1, max_size=3)
        try:
            n = await create_reminders_for_user(pool, args.user_id, args.year)
            print(f"Создано напоминаний: {n}")
        finally:
            await pool.close()
        return

    regimes = tuple(args.regimes.split(",")) if args.regimes else None
    _print_deadlines(get_deadlines(args.year, regimes))


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--year", type=int, default=date.today().year)
    parser.add_argument("--regimes", default=None,
                        help="через запятую: УСН,Патент,НПД,ОСНО,ИП")
    parser.add_argument("--user-id", type=int, default=None,
                        help="записать напоминания пользователю в reminders")
    parser.add_argument("--dsn", default=None)
    args = parser.parse_args()
    asyncio.run(_main_async(args))


if __name__ == "__main__":
    main()