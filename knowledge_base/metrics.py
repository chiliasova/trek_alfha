import os
import sqlite3
import pandas as pd
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

plt.rcParams["font.family"] = "DejaVu Sans"


def plot_dau(db_path="alfa_course.db", out_png="docs/retention_chart.png"):
    os.makedirs("docs", exist_ok=True)
    conn = sqlite3.connect(db_path)
    df = pd.read_sql_query(
        "SELECT user_id, created_at FROM events WHERE event_type='bot_start'",
        conn,
    )
    conn.close()

    if df.empty:
        print("Нет событий 'bot_start' — сначала запусти mock_data.py")
        return

    df["created_at"] = pd.to_datetime(df["created_at"])
    df["date"] = df["created_at"].dt.date

    dau = (df.groupby("date")["user_id"]
             .nunique()
             .reset_index()
             .rename(columns={"user_id": "dau"}))

    plt.figure(figsize=(10, 5))
    plt.plot(dau["date"], dau["dau"],
             marker="o", color="#EF3124", linewidth=2)
    plt.title("Динамика активных пользователей бота (DAU)")
    plt.xlabel("Дата")
    plt.ylabel("Уникальные пользователи")
    plt.xticks(rotation=45)
    plt.grid(True, alpha=0.3)
    plt.tight_layout()
    plt.savefig(out_png, dpi=300)
    plt.close()

    print(f"График сохранён: {out_png}")
    print(f"Всего уникальных пользователей: {df['user_id'].nunique()}")
    print(f"Средний DAU: {dau['dau'].mean():.1f}")
    print(f"Пиковый DAU: {dau['dau'].max()}")


if __name__ == "__main__":
    plot_dau()