import sqlite3
import json
from datetime import datetime

def log_event(user_id: int, event_type: str, metadata: dict | None = None, db_path: str = "alfa_course.db"):
    """
    Записывает событие в базу данных.
    
    :param user_id: Telegram ID пользователя
    :param event_type: Тип события (например, 'bot_start', 'ask_question', 'view_material')
    :param metadata: Дополнительные данные в виде словаря (например, {'text': 'вопрос пользователя'})
    :param db_path: Путь к файлу базы данных SQLite
    """
    try:
        conn = sqlite3.connect(db_path)
        conn.execute(
            "INSERT INTO events (user_id, event_type, metadata, created_at) VALUES (?, ?, ?, ?)",
            (
                user_id, 
                event_type,
                json.dumps(metadata or {}, ensure_ascii=False),
                datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            ),
        )
        conn.commit()
        conn.close()
    except sqlite3.Error as e:
        print(f"Ошибка логирования события: {e}")