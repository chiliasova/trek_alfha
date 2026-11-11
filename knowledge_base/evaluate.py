import random

random.seed(42)

TEST_SET = [
    # Прямые вопросы - ответ есть в базе
    {"q": "Какой налог платит ИП на УСН?",
     "expected_id": "taxes_usn.txt__chunk_001", "type": "direct"},
    {"q": "Как зарегистрироваться на Ozon?",
     "expected_id": "marketing_ozon.md__chunk_001", "type": "direct"},
    {"q": "Какую комиссию берёт Ozon?",
     "expected_id": "marketing_ozon.md__chunk_002", "type": "direct"},
    {"q": "Когда сдавать декларацию по УСН?",
     "expected_id": "taxes_usn.txt__chunk_002", "type": "direct"},
    {"q": "Какую нишу выбрать на Ozon?",
     "expected_id": "marketing_ozon.md__chunk_000", "type": "direct"},
    {"q": "Что делать при превышении 60 млн на УСН?",
     "expected_id": "taxes_usn.txt__chunk_003", "type": "direct"},
    # Неоднозначные - бот должен уточнить
    {"q": "Помогите с бизнесом", "expected_id": None, "type": "ambiguous"},
    {"q": "Как платить налоги?", "expected_id": None, "type": "ambiguous"},
    # Вне базы - честный отказ
    {"q": "Как зарегистрировать товарный знак в США?",
     "expected_id": None, "type": "out_of_base"},
    {"q": "Сколько стоит патент в Казахстане?",
     "expected_id": None, "type": "out_of_base"},
]


def simulate_bot(question: str, qtype: str):
    """Имитация RAG-бота. Заменить на вызов бота Яны при интеграции."""
    if qtype == "out_of_base":
        return {"status": "refusal"}
    if qtype == "ambiguous":
        return {"status": "clarification"}
    # direct - 85% правильного попадания в топ-3
    if random.random() < 0.85:
        return {"status": "success", "top_ids": [question["expected_id"]]}
    return {"status": "success", "top_ids": ["wrong__id"]}


def evaluate():
    hit, total_rel = 0, 0
    honest, total_oob = 0, 0

    for t in TEST_SET:
        res = simulate_bot(t, t["type"])
        if t["type"] == "out_of_base":
            total_oob += 1
            if res["status"] == "refusal":
                honest += 1
        elif t["type"] == "direct":
            total_rel += 1
            if res["status"] == "success" and \
               t["expected_id"] in res["top_ids"]:
                hit += 1

    print("📊 Оценка качества бота")
    print(f"Hit@3 (точность выдачи):     "
          f"{(hit / total_rel * 100):.1f}%  (цель > 80%)")
    print(f"Доля честных отказов:        "
          f"{(honest / total_oob * 100):.1f}%  (цель = 100%)")


if __name__ == "__main__":
    evaluate()