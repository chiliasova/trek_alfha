from datetime import datetime


TAX_CALENDAR = {
    "УСН": [
        {"month": 4,  "day": 28, "event": "Авансовый платёж за 1 квартал"},
        {"month": 7,  "day": 28, "event": "Авансовый платёж за 2 квартал"},
        {"month": 10, "day": 28, "event": "Авансовый платёж за 3 квартал"},
        {"month": 4,  "day": 28, "event": "Уплата налога за год (ИП)"},
    ],
    "НПД": [
        {"month": 2, "day": 28, "event": "Уплата налога за январь"},
        {"month": 3, "day": 28, "event": "Уплата налога за февраль"},
        {"month": 4, "day": 28, "event": "Уплата налога за март"},
    ],
    "Патент": [
        {"month": 3, "day": 31,
         "event": "Оплата 1/3 стоимости патента (если срок > 6 мес.)"},
    ],
    "ОСНО": [
        {"month": 4, "day": 28, "event": "НДС и налог на прибыль за 1 квартал"},
        {"month": 7, "day": 28, "event": "НДС и налог на прибыль за 2 квартал"},
    ],
}


def get_upcoming_taxes(tax_regime: str, days_ahead: int = 30) -> list[dict]:
    """Возвращает предстоящие налоговые события для режима."""
    if tax_regime not in TAX_CALENDAR:
        return []

    today = datetime.now()
    out = []
    for tax in TAX_CALENDAR[tax_regime]:
        date = datetime(today.year, tax["month"], tax["day"])
        if date < today:
            date = datetime(today.year + 1, tax["month"], tax["day"])
        delta = (date - today).days
        if 0 <= delta <= days_ahead:
            out.append({
                "date": date.strftime("%Y-%m-%d"),
                "event": tax["event"],
                "days_left": delta,
                "message": f"Напоминание: {tax['event']} — "
                           f"{date.strftime('%d.%m.%Y')}",
            })
    return out


if __name__ == "__main__":
    print("Тест налогового календаря (УСН, 365 дней):")
    for item in get_upcoming_taxes("УСН", days_ahead=365):
        print(" •", item["message"])