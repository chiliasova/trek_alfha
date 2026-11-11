import sqlite3
import random
import json
from datetime import datetime, timedelta


def create_mock_db(path: str = "alfa_course.db"):
    random.seed(42)
    conn = sqlite3.connect(path)
    cur = conn.cursor()

    with open("schema.sql", "r", encoding="utf-8") as f:
        cur.executescript(f.read())

    stages = ["idea", "start", "growth"]
    industries = ["ecommerce", "services", "it", "retail"]
    regimes = ["УСН", "Патент", "НПД", "ОСНО"]
    experiences = ["novice", "experienced"]

    for user_id in range(1000, 1050):
        cur.execute("INSERT INTO users (user_id) VALUES (?)", (user_id,))
        cur.execute(
            """INSERT INTO business_profiles
               (user_id, business_stage, industry, tax_regime, experience_level)
               VALUES (?, ?, ?, ?, ?)""",
            (user_id,
             random.choice(stages),
             random.choice(industries),
             random.choice(regimes),
             random.choice(experiences)),
        )

        # Первый визит - всегда bot_start
        first_day = random.randint(0, 13)
        first_dt = (datetime.now() - timedelta(days=first_day)) \
            .strftime("%Y-%m-%d %H:%M:%S")
        cur.execute(
            "INSERT INTO events (user_id, event_type, metadata, created_at) "
            "VALUES (?, ?, ?, ?)",
            (user_id, "bot_start", json.dumps({"source": "mock"}), first_dt),
        )

        # Последующие действия (в т.ч. возвраты)
        for _ in range(random.randint(1, 6)):
            days_ago = random.randint(0, 13)
            dt = (datetime.now() - timedelta(days=days_ago)) \
                .strftime("%Y-%m-%d %H:%M:%S")
            if random.random() < 0.3:
                etype = "bot_start"
            else:
                etype = random.choice(
                    ["ask_question", "view_material", "reminder_created"])
            cur.execute(
                "INSERT INTO events (user_id, event_type, metadata, created_at) "
                "VALUES (?, ?, ?, ?)",
                (user_id, etype, json.dumps({"source": "mock"}), dt),
            )

    conn.commit()
    conn.close()
    print(f"База {path} создана: 50 пользователей, события за 14 дней.")


if __name__ == "__main__":
    create_mock_db()