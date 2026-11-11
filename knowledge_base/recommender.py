import json


def score_material(profile: dict, material: dict) -> tuple[int, list[str]]:
    """Возвращает (балл, список причин) для честного объяснения."""
    score = 0
    reasons = []

    if profile.get("industry") == material.get("topic"):
        score += 10
        reasons.append(f"тема совпадает с вашей сферой ({material['topic']})")

    if profile.get("business_stage") == material.get("target_stage"):
        score += 5
        reasons.append(f"стадия «{material['target_stage']}» совпадает с вашей")

    if profile.get("experience_level") == "novice" \
            and material.get("level") == "beginner":
        score += 3
        reasons.append("уровень подходит новичку")
    elif profile.get("experience_level") == "experienced" \
            and material.get("level") == "advanced":
        score += 3
        reasons.append("уровень подходит опытному предпринимателю")

    if profile.get("experience_level") == "novice" \
            and not material.get("is_paid", False):
        score += 2
        reasons.append("бесплатный — хороший старт")

    return score, reasons


def recommend_materials(profile: dict, materials: list[dict],
                        top_n: int = 3) -> list[dict]:
    """Топ-N материалов с честным объяснением, сортировкой и cold-start fallback."""
    scored = []
    for m in materials:
        score, reasons = score_material(profile, m)
        scored.append({
            "score": score,
            "material": m,
            "reasons": reasons,
            "is_fallback": False,
        })

    # Основной пул — только совпавшие материалы
    matched = [x for x in scored if x["score"] > 0]
    matched.sort(
        key=lambda x: (
            x["material"].get("is_paid", False),
            -x["score"],
            x["material"].get("topic") != profile.get("industry"),
        )
    )

    if len(matched) < top_n:
        used_titles = {x["material"]["title"] for x in matched}
        fallback_pool = [
            x for x in scored
            if x["score"] == 0
            and not x["material"].get("is_paid", False)
            and x["material"]["title"] not in used_titles
        ]
        for x in fallback_pool[:top_n - len(matched)]:
            x["is_fallback"] = True
            x["reasons"] = ["популярный бесплатный материал для старта"]
            matched.append(x)

    out = []
    for item in matched[:top_n]:
        m = item["material"]
        explanation = "Подходит: " + "; ".join(item["reasons"]) + "."
        next_step = (
            "Платный курс: доступно краткое описание и страница курса."
            if m.get("is_paid") else
            "Бесплатный материал — можно изучать сразу."
        )
        out.append({
            "title": m.get("title"),
            "url": m.get("url"),
            "is_paid": m.get("is_paid"),
            "score": item["score"],
            "is_fallback": item["is_fallback"],
            "explanation": explanation,
            "next_step": next_step,
        })
    return out


def load_materials_from_chunks(path: str = "output/chunks.jsonl") -> list[dict]:
    """Дедуплицирует чанки в список материалов (уникальные title)."""
    seen, materials = set(), []
    with open(path, encoding="utf-8") as f:
        for line in f:
            c = json.loads(line)
            if c["title"] in seen:
                continue
            seen.add(c["title"])
            materials.append({
                "title": c["title"],
                "topic": c["topic"],
                "target_stage": c["target_stage"],
                "level": c["level"],
                "is_paid": c["is_paid"],
                "url": c["url"],
            })
    return materials


if __name__ == "__main__":
    import sys
    import os

    if "--demo" in sys.argv:
        profile = {
            "industry": "ecommerce",
            "business_stage": "start",
            "experience_level": "novice",
        }
        materials = [
            {"title": "Как запустить магазин на Ozon",
             "topic": "ecommerce", "target_stage": "start",
             "level": "beginner", "is_paid": False, "url": "u1"},
            {"title": "Продвинутая аналитика маркетплейсов",
             "topic": "ecommerce", "target_stage": "growth",
             "level": "advanced", "is_paid": True, "url": "u2"},
            {"title": "УСН: как считать и платить налог",
             "topic": "taxes", "target_stage": "start",
             "level": "beginner", "is_paid": False, "url": "u3"},
        ]
    else:
        chunks_path = "output/chunks.jsonl"
        if not os.path.exists(chunks_path):
            print(f"Нет файла {chunks_path}. Сначала запусти: python chunker.py")
            print("    Или запусти демо: python recommender.py --demo")
            sys.exit(1)
        profile = {
            "industry": "marketing",
            "business_stage": "start",
            "experience_level": "novice",
        }
        materials = load_materials_from_chunks(chunks_path)

    print(json.dumps(
        recommend_materials(profile, materials),
        ensure_ascii=False, indent=2))